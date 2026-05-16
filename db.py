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
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT task_id FROM task_notes")
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
