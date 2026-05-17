# main.py
import hashlib
import hmac
import json
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request

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

DASHBOARD_USER = os.environ.get("DASHBOARD_USER", "")
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
_basic = HTTPBasic(auto_error=False)


def require_auth(creds: Optional[HTTPBasicCredentials] = Depends(_basic)) -> None:
    """Gate dashboard + /api/* with Basic auth. Webhook and /healthz are exempt."""
    if not DASHBOARD_USER or not DASHBOARD_PASSWORD:
        # Misconfigured server — fail closed.
        raise HTTPException(status_code=503, detail="auth not configured")
    if creds is None or not (
        secrets.compare_digest(creds.username, DASHBOARD_USER)
        and secrets.compare_digest(creds.password, DASHBOARD_PASSWORD)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )


db.init_db()


CACHE_KEY = "fetch_raw"
CACHE_TTL = 15 * 60


def _fetch_raw() -> dict:
    cached = db.cache_load(CACHE_KEY, CACHE_TTL)
    if cached:
        return cached

    tasks = clickup_client.get_all_tasks(TEAM_ID, USER_ID)
    api_task_ids = {t["id"] for t in tasks}

    # Include DB-tracked tasks not in the API result (e.g. handed off to reviewer) — parallel fetch
    missing_ids = list(db.get_tracked_task_ids() - api_task_ids)
    if missing_ids:
        with ThreadPoolExecutor(max_workers=10) as pool:
            for extra in pool.map(clickup_client.get_task, missing_ids):
                if not extra.get("err"):
                    tasks.append(extra)

    # Fetch handoff_ms: serve known values from DB, only hit ClickUp for the rest.
    all_ids = [t["id"] for t in tasks]
    cached_handoffs = db.get_handoff_cache(all_ids)
    to_fetch = [t for t in tasks if t["id"] not in cached_handoffs]
    fresh: dict[str, Optional[str]] = {}
    if to_fetch:
        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(clickup_client.get_handoff_ms, to_fetch))
        fresh = {t["id"]: h for t, h in zip(to_fetch, results)}
        db.save_handoff_cache(fresh)

    handoffs = {**cached_handoffs, **fresh}
    payload = {"tasks": tasks, "handoffs": handoffs}
    db.cache_save(CACHE_KEY, payload)
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

    def _ms_to_dt(value):
        if not value:
            return None
        try:
            return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
        except (ValueError, OSError, TypeError):
            return None

    task_metrics = []
    for task in raw["tasks"]:
        updated = _ms_to_dt(task.get("date_updated"))
        if not updated or not (filter_start <= updated <= filter_end):
            continue

        tm = m.calculate_task_metrics(
            task_id=task["id"],
            task_name=task["name"],
            deadline_ms=clickup_client.get_deadline_ms(task),
            handoff_ms=raw["handoffs"].get(task["id"]),
            date_created_ms=task.get("date_created"),
        )
        if tm.first_handoff:
            db.ensure_handoff_done(tm.task_id)
        ref_date = tm.first_handoff or tm.deadline
        if ref_date and not (filter_start <= ref_date <= filter_end):
            continue
        task_metrics.append(tm)

    # Sort newest-created first; tasks without date_created go to the bottom
    task_metrics.sort(
        key=lambda t: t.date_created or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    summary = m.calculate_summary(task_metrics)
    return task_metrics, summary


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse,
               dependencies=[Depends(require_auth)])
async def dashboard(
    request: Request,
    days: int = Query(default=30),
    start: Optional[str] = Query(default=None),
    end: Optional[str] = Query(default=None),
    show_hidden: int = Query(default=0),
):
    task_metrics, summary = _build_metrics(days, start, end)
    hidden_ids = db.get_hidden_task_ids()
    if not show_hidden:
        task_metrics = [t for t in task_metrics if t.task_id not in hidden_ids]
        summary = m.calculate_summary(task_metrics)
    notes = {t.task_id: db.get_note(t.task_id) for t in task_metrics}
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "summary": summary,
        "tasks": task_metrics,
        "notes": notes,
        "days": days,
        "start": start or "",
        "end": end or "",
        "show_hidden": bool(show_hidden),
        "hidden_count": len(hidden_ids),
    })


@app.api_route("/healthz", methods=["GET", "HEAD"])
async def healthz():
    return {"ok": True}


@app.get("/api/refresh", dependencies=[Depends(require_auth)])
async def refresh():
    db.cache_clear(CACHE_KEY)
    return RedirectResponse(url="/")


@app.get("/api/notes/{task_id}", dependencies=[Depends(require_auth)])
async def get_note(task_id: str):
    return db.get_note(task_id)


# ── Notatki (iteracje + komentarz) ────────────────────────────────────────────

@app.post("/api/notes/{task_id}", dependencies=[Depends(require_auth)])
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


@app.delete("/api/notes/{task_id}/iterations", dependencies=[Depends(require_auth)])
async def clear_manual_iterations(task_id: str):
    """Przywróć wartość automatyczną (usuń manual override)."""
    db.clear_manual_iterations(task_id)
    return {"ok": True}


@app.post("/api/notes/{task_id}/hide", dependencies=[Depends(require_auth)])
async def hide_task(task_id: str):
    db.set_hidden(task_id, True)
    return {"ok": True}


@app.delete("/api/notes/{task_id}/hide", dependencies=[Depends(require_auth)])
async def unhide_task(task_id: str):
    db.set_hidden(task_id, False)
    return {"ok": True}


# ── Webhook ClickUp ───────────────────────────────────────────────────────────

@app.post("/webhooks/clickup")
async def clickup_webhook(request: Request):
    import time
    body = await request.body()
    received_at = time.time()

    sig_ok = True
    secret = os.environ.get("CLICKUP_WEBHOOK_SECRET", "")
    if secret:
        signature = request.headers.get("X-Signature", "")
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        sig_ok = hmac.compare_digest(signature, expected)

    try:
        payload = json.loads(body) if body else {}
    except Exception:
        payload = {}

    event_type = payload.get("event")
    raw_str = body.decode("utf-8", errors="replace")[:4000]

    if not sig_ok:
        db.log_webhook_event(received_at, event_type, None, None, None, False, raw_str)
        return JSONResponse({"ok": False, "error": "invalid signature"}, status_code=401)

    for item in payload.get("history_items", []):
        task_id = payload.get("task_id") or item.get("parent_id")
        before = item.get("before") or {}
        after = item.get("after") or {}
        before_id = before.get("id")
        after_id = after.get("id")

        db.log_webhook_event(
            received_at, event_type,
            str(task_id) if task_id else None,
            int(before_id) if before_id is not None else None,
            int(after_id) if after_id is not None else None,
            True, raw_str,
        )

        if task_id:
            db.process_assignee_event(
                task_id=str(task_id),
                before_id=int(before_id) if before_id is not None else None,
                after_id=int(after_id) if after_id is not None else None,
                user_id=USER_ID,
            )

    return {"ok": True}


@app.get("/api/debug-webhooks", dependencies=[Depends(require_auth)])
async def debug_webhooks():
    return clickup_client.list_webhooks(TEAM_ID)


@app.get("/api/debug-task/{task_id}", dependencies=[Depends(require_auth)])
async def debug_task_quick(task_id: str):
    task = clickup_client.get_task(task_id)
    return {
        "status": task.get("status", {}).get("status"),
        "assignees": [{"id": a.get("id"), "username": a.get("username")} for a in task.get("assignees", [])],
        "date_closed": task.get("date_closed"),
    }


@app.get("/api/debug-events", dependencies=[Depends(require_auth)])
async def debug_events(limit: int = 50):
    return db.get_recent_webhook_events(limit)


@app.get("/api/track-task/{task_id}", dependencies=[Depends(require_auth)])
async def track_task(task_id: str):
    """Manually add a task to DB so it appears in dashboard even if not currently assigned to user."""
    db.track_task(task_id)
    db.cache_clear(CACHE_KEY)
    return {"ok": True, "task_id": task_id}


@app.get("/api/setup-webhook", dependencies=[Depends(require_auth)])
async def setup_webhook(app_url: str = Query(..., description="Pełny URL aplikacji, np. https://xxx.onrender.com")):
    """Jednorazowa rejestracja webhooka w ClickUp. Wywołaj raz po deployu."""
    endpoint = f"{app_url.rstrip('/')}/webhooks/clickup"
    result = clickup_client.register_webhook(TEAM_ID, endpoint)
    return result


@app.get("/api/reregister-webhook", dependencies=[Depends(require_auth)])
async def reregister_webhook(app_url: str = Query(..., description="Pełny URL aplikacji, np. https://clickup-94jg.onrender.com")):
    """Skasuj wszystkie istniejące webhooki i zarejestruj nowy. Zwraca secret do wpisania w env CLICKUP_WEBHOOK_SECRET."""
    existing = clickup_client.list_webhooks(TEAM_ID).get("webhooks", [])
    deleted = []
    for wh in existing:
        result = clickup_client.delete_webhook(wh["id"])
        deleted.append({"id": wh["id"], "result": result})

    endpoint = f"{app_url.rstrip('/')}/webhooks/clickup"
    new_webhook = clickup_client.register_webhook(TEAM_ID, endpoint)
    return {"deleted": deleted, "new_webhook": new_webhook}
