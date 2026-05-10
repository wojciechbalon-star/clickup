# main.py
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request

import cache
import clickup_client
import metrics as m

load_dotenv()

app = FastAPI()
templates = Jinja2Templates(directory="templates")

_missing = [v for v in ("CLICKUP_TEAM_ID", "CLICKUP_USER_ID", "CLICKUP_TOKEN") if not os.environ.get(v)]
if _missing:
    raise SystemExit(f"Missing required env vars: {', '.join(_missing)}")

TEAM_ID = os.environ["CLICKUP_TEAM_ID"]
USER_ID = int(os.environ["CLICKUP_USER_ID"])


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


@app.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    days: int = Query(default=30),
    start: Optional[str] = Query(default=None),
    end: Optional[str] = Query(default=None),
):
    task_metrics, summary = _build_metrics(days, start, end)
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "summary": summary,
        "tasks": task_metrics,
        "days": days,
        "start": start or "",
        "end": end or "",
    })


@app.get("/api/refresh")
async def refresh():
    cache.clear_cache()
    return RedirectResponse(url="/")


@app.get("/api/debug-task/{task_id}")
async def debug_task(task_id: str):
    import requests as req
    headers = {"Authorization": os.environ["CLICKUP_TOKEN"]}
    r = req.get(
        f"https://api.clickup.com/api/v2/task/{task_id}/time_in_status",
        headers=headers,
        timeout=15,
    )
    return {"status": r.status_code, "full": r.json()}
