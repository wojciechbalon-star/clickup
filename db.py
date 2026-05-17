import os
import time
from typing import Optional
import psycopg2

DATABASE_URL = os.environ["DATABASE_URL"]


def _conn():
    return psycopg2.connect(DATABASE_URL)


def init_db() -> None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS task_notes (
                task_id           TEXT    PRIMARY KEY,
                auto_iterations   INTEGER DEFAULT 0,
                manual_iterations INTEGER,
                comment           TEXT    DEFAULT '',
                handoff_done      BOOLEAN DEFAULT FALSE,
                updated_at        DOUBLE PRECISION DEFAULT 0
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS webhook_events (
                id           BIGSERIAL PRIMARY KEY,
                received_at  DOUBLE PRECISION,
                event_type   TEXT,
                task_id      TEXT,
                before_id    BIGINT,
                after_id     BIGINT,
                sig_ok       BOOLEAN,
                raw_payload  TEXT
            )
        """)
        # Cache of first-handoff timestamps (immutable once known) so we don't
        # re-fetch ClickUp's time_in_status on every dashboard render.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS task_handoff_cache (
                task_id      TEXT PRIMARY KEY,
                handoff_ms   TEXT,
                fetched_at   DOUBLE PRECISION
            )
        """)
        # Single-row JSON blob cache shared across machines (replaces cache.json).
        cur.execute("""
            CREATE TABLE IF NOT EXISTS app_cache (
                key        TEXT PRIMARY KEY,
                payload    TEXT,
                updated_at DOUBLE PRECISION
            )
        """)


def get_note(task_id: str) -> dict:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT auto_iterations, manual_iterations, comment, handoff_done "
            "FROM task_notes WHERE task_id=%s",
            (task_id,),
        )
        row = cur.fetchone()
    if row:
        return {
            "auto_iterations":   row[0],
            "manual_iterations": row[1],
            "comment":           row[2] or "",
            "handoff_done":      bool(row[3]),
        }
    return {"auto_iterations": 0, "manual_iterations": None, "comment": "", "handoff_done": False}


def effective_iterations(note: dict) -> int:
    """Manual override takes precedence over auto."""
    if note["manual_iterations"] is not None:
        return note["manual_iterations"]
    return note["auto_iterations"]


def ensure_handoff_done(task_id: str) -> None:
    """Mark handoff_done=True from API data without touching iteration count."""
    note = get_note(task_id)
    if note["handoff_done"]:
        return
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO task_notes (task_id, handoff_done, updated_at)
            VALUES (%s, TRUE, %s)
            ON CONFLICT (task_id) DO UPDATE SET
                handoff_done = TRUE, updated_at = EXCLUDED.updated_at
        """, (task_id, time.time()))


def track_task(task_id: str) -> None:
    """Ensure a row exists for this task so the dashboard fetches it even if no longer assigned to user."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO task_notes (task_id, updated_at) VALUES (%s, %s)
            ON CONFLICT (task_id) DO UPDATE SET updated_at = EXCLUDED.updated_at
        """, (task_id, time.time()))


def get_tracked_task_ids() -> set[str]:
    """Tasks worth refetching when missing from the assignee list: only those with
    real activity (iterations counted, manual override, or a note). Bootstrap-only
    rows from ensure_handoff_done aren't re-pulled — they were never the user's task."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT task_id FROM task_notes
            WHERE auto_iterations > 0
               OR manual_iterations IS NOT NULL
               OR COALESCE(comment, '') <> ''
        """)
        rows = cur.fetchall()
    return {r[0] for r in rows}


def save_manual(task_id: str, iterations: Optional[int], comment: Optional[str]) -> None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO task_notes (task_id, updated_at) VALUES (%s, %s)
            ON CONFLICT (task_id) DO UPDATE SET updated_at = EXCLUDED.updated_at
        """, (task_id, time.time()))
        if iterations is not None:
            cur.execute(
                "UPDATE task_notes SET manual_iterations=%s WHERE task_id=%s",
                (iterations, task_id),
            )
        if comment is not None:
            cur.execute(
                "UPDATE task_notes SET comment=%s WHERE task_id=%s",
                (comment, task_id),
            )


def clear_manual_iterations(task_id: str) -> None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE task_notes SET manual_iterations=NULL WHERE task_id=%s", (task_id,))


def process_assignee_event(task_id: str, before_id: Optional[int],
                           after_id: Optional[int], user_id: int) -> None:
    """
    Ty → inny (pierwszy raz) → handoff_done = True, nie liczy iteracji.
    inny → Ty (po handoffie)  → +1 iteracja.
    Kolejne Ty → inny          → ignorowane (handoff już był).
    """
    user_involved = (before_id == user_id) or (after_id == user_id)
    if not user_involved:
        return

    note = get_note(task_id)
    now = time.time()

    with _conn() as conn, conn.cursor() as cur:
        # Always create a tracking row so dashboard can fetch this task later
        cur.execute("""
            INSERT INTO task_notes (task_id, updated_at) VALUES (%s, %s)
            ON CONFLICT (task_id) DO UPDATE SET updated_at = EXCLUDED.updated_at
        """, (task_id, now))

        if before_id == user_id and after_id != user_id and not note["handoff_done"]:
            # pierwszy handoff
            cur.execute("""
                INSERT INTO task_notes (task_id, handoff_done, updated_at)
                VALUES (%s, TRUE, %s)
                ON CONFLICT (task_id) DO UPDATE SET
                    handoff_done = TRUE, updated_at = EXCLUDED.updated_at
            """, (task_id, now))

        elif after_id == user_id and before_id != user_id and note["handoff_done"]:
            # task wrócił do Ciebie po handoffie → iteracja
            cur.execute("""
                INSERT INTO task_notes (task_id, auto_iterations, handoff_done, updated_at)
                VALUES (%s, 1, TRUE, %s)
                ON CONFLICT (task_id) DO UPDATE SET
                    auto_iterations = task_notes.auto_iterations + 1,
                    updated_at      = EXCLUDED.updated_at
            """, (task_id, now))


def log_webhook_event(received_at: float, event_type: Optional[str],
                     task_id: Optional[str], before_id: Optional[int],
                     after_id: Optional[int], sig_ok: bool, raw_payload: str) -> None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO webhook_events
            (received_at, event_type, task_id, before_id, after_id, sig_ok, raw_payload)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (received_at, event_type, task_id, before_id, after_id, sig_ok, raw_payload))


def get_handoff_cache(task_ids: list[str]) -> dict[str, Optional[str]]:
    """Return task_id -> handoff_ms for any cached rows. Only non-NULL values are
    served from cache; tasks with NULL are refetched in case a handoff has since
    occurred. Rows older than 7 days are also ignored (handles edge cases like
    a handoff being undone by reopening the task)."""
    if not task_ids:
        return {}
    cutoff = time.time() - 7 * 86400
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT task_id, handoff_ms FROM task_handoff_cache "
            "WHERE task_id = ANY(%s) AND handoff_ms IS NOT NULL AND fetched_at >= %s",
            (task_ids, cutoff),
        )
        rows = cur.fetchall()
    return {r[0]: r[1] for r in rows}


def save_handoff_cache(values: dict[str, Optional[str]]) -> None:
    """Bulk upsert. Pass {task_id: handoff_ms_or_None}."""
    if not values:
        return
    now = time.time()
    with _conn() as conn, conn.cursor() as cur:
        for tid, hms in values.items():
            cur.execute("""
                INSERT INTO task_handoff_cache (task_id, handoff_ms, fetched_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (task_id) DO UPDATE SET
                    handoff_ms = EXCLUDED.handoff_ms,
                    fetched_at = EXCLUDED.fetched_at
            """, (tid, hms, now))


def cache_load(key: str, ttl_seconds: int) -> Optional[dict]:
    """Read shared JSON cache blob if not older than ttl_seconds."""
    cutoff = time.time() - ttl_seconds
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT payload, updated_at FROM app_cache WHERE key = %s",
            (key,),
        )
        row = cur.fetchone()
    if not row or row[1] < cutoff:
        return None
    try:
        import json as _json
        return _json.loads(row[0])
    except (ValueError, TypeError):
        return None


def cache_save(key: str, payload: dict) -> None:
    import json as _json
    now = time.time()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO app_cache (key, payload, updated_at) VALUES (%s, %s, %s)
            ON CONFLICT (key) DO UPDATE SET
                payload = EXCLUDED.payload,
                updated_at = EXCLUDED.updated_at
        """, (key, _json.dumps(payload), now))


def cache_clear(key: str) -> None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM app_cache WHERE key = %s", (key,))


def get_recent_webhook_events(limit: int = 50) -> list[dict]:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT received_at, event_type, task_id, before_id, after_id, sig_ok, raw_payload
            FROM webhook_events ORDER BY id DESC LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
    return [
        {
            "received_at": r[0],
            "event_type": r[1],
            "task_id": r[2],
            "before_id": r[3],
            "after_id": r[4],
            "sig_ok": bool(r[5]),
            "raw_payload": r[6],
        }
        for r in rows
    ]
