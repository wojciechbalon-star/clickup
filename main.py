# main.py
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request

import cache
import clickup_client
import db
import metrics as m

load_dotenv()

app = FastAPI()
templates = Jinja2Templates(directory="templates")

_missing = [v for v in ("CLICKUP_TEAM_ID", "CLICKUP_USER_ID", "CLICKUP_TOKEN") if not os.environ.get(v)]
if _missing:
    raise SystemExit(f"Missing required env vars: {', '.join(_missing)}")

TEAM_ID = os.environ["CLICKUP_TEAM_ID"]
USER_ID = int(os.environ["CLICKUP_USER_ID"])

db.init_db()


def _fetch_raw() -> dict:
    cached = cache.load_cache()
    if cached:
        return cached

    tasks = clickup_client.get_all_tasks(TEAM_ID, USER_ID)
    handoffs = {t["id"]: clickup_client.get_handoff_ms(t["id"]) for t in tasks}
    payload = {"tasks": tasks, "handoffs": handoffs}
    cache.save_cache(payload)
    return payload


def _build_metrics(days: int, start: Optional[str], end: Optional[str]) -> tuple[list, m.Summary]:
    raw = _fetch_raw()

    now = datetime.now(tz=timezone.utc)
    if start and end:
        def _parse_date(s: str) -> datetime:
            dt = datetime.fromisoformat(s)
            return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        filter_start = _parse_date(start)
        filter_end = _parse_date(end)
    else:
        filter_start = now - timedelta(days=days)
        filter_end = now

    task_metrics = []
    for task in raw["tasks"]:
        tm = m.calculate_task_metrics(
            task_id=task["id"],
            task_name=task["name"],
            deadline_ms=clickup_client.get_deadline_ms(task),
            handoff_ms=raw["handoffs"].get(task["id"]),
        )
        ref_date = tm.first_handoff or tm.deadline
        if ref_date and not (filter_start <= ref_date <= filter_end):
            continue
        task_metrics.append(tm)

    summary = m.calculate_summary(task_metrics)
    return task_metrics, summary


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    days: int = Query(default=30),
    start: Optional[str] = Query(default=None),
    end: Optional[str] = Query(default=None),
):
    task_metrics, summary = _build_metrics(days, start, end)
    notes = {t.task_id: db.get_note(t.task_id) for t in task_metrics}
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "summary": summary,
        "tasks": task_metrics,
        "notes": notes,
        "days": days,
        "start": start or "",
        "end": end or "",
    })


@app.get("/api/refresh")
async def refresh():
    cache.clear_cache()
    return RedirectResponse(url="/")


@app.get("/api/notes/{task_id}")
async def get_note(task_id: str):
    return db.get_note(task_id)


# ── Notatki (iteracje + komentarz) ────────────────────────────────────────────

@app.post("/api/notes/{task_id}")
async def save_note(task_id: str, request: Request):
    body = await request.json()
    iterations = body.get("iterations")
    comment = body.get("comment")
    db.save_manual(
        task_id,
        iterations=int(iterations) if iterations is not None else None,
        comment=comment,
    )
    return {"ok": True}


@app.delete("/api/notes/{task_id}/iterations")
async def clear_manual_iterations(task_id: str):
    """Przywróć wartość automatyczną (usuń manual override)."""
    db.save_manual(task_id, iterations=None, comment=None)
    import sqlite3
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute("UPDATE task_notes SET manual_iterations=NULL WHERE task_id=?", (task_id,))
    return {"ok": True}


# ── Webhook ClickUp ───────────────────────────────────────────────────────────

@app.post("/webhooks/clickup")
async def clickup_webhook(request: Request):
    body = await request.body()

    secret = os.environ.get("CLICKUP_WEBHOOK_SECRET", "")
    if secret:
        signature = request.headers.get("X-Signature", "")
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return JSONResponse({"ok": False, "error": "invalid signature"}, status_code=401)

    try:
        payload = json.loads(body)
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)

    for item in payload.get("history_items", []):
        task_id = item.get("parent_id") or payload.get("task_id")
        before = item.get("before") or {}
        after = item.get("after") or {}
        before_id = before.get("id")
        after_id = after.get("id")

        if task_id:
            db.process_assignee_event(
                task_id=str(task_id),
                before_id=int(before_id) if before_id is not None else None,
                after_id=int(after_id) if after_id is not None else None,
                user_id=USER_ID,
            )

    return {"ok": True}


@app.get("/api/setup-webhook")
async def setup_webhook(app_url: str = Query(..., description="Pełny URL aplikacji, np. https://xxx.onrender.com")):
    """Jednorazowa rejestracja webhooka w ClickUp. Wywołaj raz po deployu."""
    endpoint = f"{app_url.rstrip('/')}/webhooks/clickup"
    result = clickup_client.register_webhook(TEAM_ID, endpoint)
    return result
