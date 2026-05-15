import sqlite3
import time
from pathlib import Path
from typing import Optional

DB_PATH = Path("data.db")


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_notes (
                task_id           TEXT    PRIMARY KEY,
                auto_iterations   INTEGER DEFAULT 0,
                manual_iterations INTEGER,
                comment           TEXT    DEFAULT '',
                handoff_done      INTEGER DEFAULT 0,
                updated_at        REAL    DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS webhook_events (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                received_at  REAL,
                event_type   TEXT,
                task_id      TEXT,
                before_id    INTEGER,
                after_id     INTEGER,
                sig_ok       INTEGER,
                raw_payload  TEXT
            )
        """)


def log_webhook_event(received_at: float, event_type: Optional[str],
                     task_id: Optional[str], before_id: Optional[int],
                     after_id: Optional[int], sig_ok: bool, raw_payload: str) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO webhook_events
            (received_at, event_type, task_id, before_id, after_id, sig_ok, raw_payload)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (received_at, event_type, task_id, before_id, after_id, int(sig_ok), raw_payload))


def get_recent_webhook_events(limit: int = 50) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("""
            SELECT received_at, event_type, task_id, before_id, after_id, sig_ok, raw_payload
            FROM webhook_events ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()
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


def get_note(task_id: str) -> dict:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT auto_iterations, manual_iterations, comment, handoff_done "
            "FROM task_notes WHERE task_id=?",
            (task_id,),
        ).fetchone()
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
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO task_notes (task_id, handoff_done, updated_at)
            VALUES (?, 1, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                handoff_done = 1, updated_at = excluded.updated_at
        """, (task_id, time.time()))


def track_task(task_id: str) -> None:
    """Ensure a row exists for this task so the dashboard fetches it even if no longer assigned to user."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO task_notes (task_id, updated_at) VALUES (?, ?)
            ON CONFLICT(task_id) DO UPDATE SET updated_at = excluded.updated_at
        """, (task_id, time.time()))


def get_tracked_task_ids() -> set[str]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT task_id FROM task_notes").fetchall()
    return {r[0] for r in rows}


def save_manual(task_id: str, iterations: Optional[int], comment: Optional[str]) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO task_notes (task_id, updated_at) VALUES (?, ?)
            ON CONFLICT(task_id) DO UPDATE SET updated_at = excluded.updated_at
        """, (task_id, time.time()))
        if iterations is not None:
            conn.execute(
                "UPDATE task_notes SET manual_iterations=? WHERE task_id=?",
                (iterations, task_id),
            )
        if comment is not None:
            conn.execute(
                "UPDATE task_notes SET comment=? WHERE task_id=?",
                (comment, task_id),
            )


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

    with sqlite3.connect(DB_PATH) as conn:
        # Always create a tracking row so dashboard can fetch this task later
        conn.execute("""
            INSERT INTO task_notes (task_id, updated_at) VALUES (?, ?)
            ON CONFLICT(task_id) DO UPDATE SET updated_at = excluded.updated_at
        """, (task_id, now))

        if before_id == user_id and after_id != user_id and not note["handoff_done"]:
            # pierwszy handoff
            conn.execute("""
                INSERT INTO task_notes (task_id, handoff_done, updated_at)
                VALUES (?, 1, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    handoff_done = 1, updated_at = excluded.updated_at
            """, (task_id, now))

        elif after_id == user_id and before_id != user_id and note["handoff_done"]:
            # task wrócił do Ciebie po handoffie → iteracja
            conn.execute("""
                INSERT INTO task_notes (task_id, auto_iterations, handoff_done, updated_at)
                VALUES (?, 1, 1, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    auto_iterations = auto_iterations + 1,
                    updated_at      = excluded.updated_at
            """, (task_id, now))
