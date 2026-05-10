# ClickUp Developer Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a FastAPI web app that pulls ClickUp data via API and displays three developer metrics — terminowość, opóźnienie, and iteracje — on a Chart.js dashboard.

**Architecture:** FastAPI serves a Jinja2 HTML page. A JSON file cache (15 min TTL) wraps ClickUp API calls. Metrics are computed in pure Python from raw API data. The app reads three env vars: `CLICKUP_TOKEN`, `CLICKUP_USER_ID`, `CLICKUP_TEAM_ID`.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, Jinja2, Requests, Chart.js (CDN), python-dotenv, pytest, Render (deploy)

---

## File Structure

```
clickup/
├── main.py                   # FastAPI app — routes GET / and GET /api/refresh
├── clickup_client.py         # ClickUp API v2 wrapper
├── metrics.py                # Pure metric calculation logic + dataclasses
├── cache.py                  # JSON file cache with TTL
├── templates/
│   └── dashboard.html        # Jinja2 template + Chart.js
├── tests/
│   ├── __init__.py
│   ├── test_cache.py
│   └── test_metrics.py
├── requirements.txt
├── .env.example
├── .gitignore
└── render.yaml
```

---

## Task 1: Project Bootstrap

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `tests/__init__.py`

- [ ] **Step 1: Create requirements.txt**

```
fastapi==0.115.0
uvicorn[standard]==0.30.6
jinja2==3.1.4
requests==2.32.3
python-dotenv==1.0.1
pytest==8.3.3
httpx==0.27.2
```

- [ ] **Step 2: Create .env.example**

```
CLICKUP_TOKEN=pk_xxxxxxxxxxxxxxxxxxxx
CLICKUP_USER_ID=123456
CLICKUP_TEAM_ID=789012
```

- [ ] **Step 3: Create .gitignore**

```
.env
cache.json
__pycache__/
.pytest_cache/
.venv/
*.pyc
```

- [ ] **Step 4: Create tests/__init__.py**

Empty file:
```python
```

- [ ] **Step 5: Install dependencies**

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Expected: all packages install without errors.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt .env.example .gitignore tests/__init__.py
git commit -m "chore: project bootstrap"
```

---

## Task 2: cache.py

**Files:**
- Create: `cache.py`
- Create: `tests/test_cache.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_cache.py
import json
import time
from pathlib import Path
import pytest
import cache


@pytest.fixture(autouse=True)
def clean_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_FILE", tmp_path / "cache.json")


def test_load_returns_none_when_no_file():
    assert cache.load_cache() is None


def test_save_and_load_returns_payload():
    cache.save_cache({"tasks": [1, 2, 3]})
    result = cache.load_cache()
    assert result == {"tasks": [1, 2, 3]}


def test_load_returns_none_when_expired(monkeypatch):
    cache.save_cache({"tasks": []})
    monkeypatch.setattr(cache, "CACHE_TTL", -1)
    assert cache.load_cache() is None


def test_clear_removes_file():
    cache.save_cache({"tasks": []})
    cache.clear_cache()
    assert cache.load_cache() is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_cache.py -v
```

Expected: `ModuleNotFoundError: No module named 'cache'`

- [ ] **Step 3: Implement cache.py**

```python
# cache.py
import json
import time
from pathlib import Path
from typing import Optional

CACHE_FILE = Path("cache.json")
CACHE_TTL = 15 * 60  # seconds


def load_cache() -> Optional[dict]:
    if not CACHE_FILE.exists():
        return None
    data = json.loads(CACHE_FILE.read_text())
    if time.time() - data["timestamp"] > CACHE_TTL:
        return None
    return data["payload"]


def save_cache(payload: dict) -> None:
    CACHE_FILE.write_text(json.dumps({
        "timestamp": time.time(),
        "payload": payload,
    }))


def clear_cache() -> None:
    if CACHE_FILE.exists():
        CACHE_FILE.unlink()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_cache.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add cache.py tests/test_cache.py
git commit -m "feat: JSON file cache with TTL"
```

---

## Task 3: clickup_client.py

**Files:**
- Create: `clickup_client.py`

> **API note:** ClickUp API v2 does not expose a clean single "activity" endpoint for assignment history. The approach: fetch task details with `?include_task_history=true`. If the response contains no `history` key, check `activity` or consult the ClickUp API docs for your workspace plan. Print the raw response for one task during first run to verify the shape, then adjust `get_task_activity()` accordingly. The rest of the system is isolated from this — only `get_task_activity()` needs to change.

- [ ] **Step 1: Implement clickup_client.py**

```python
# clickup_client.py
import os
import requests
from typing import Optional

BASE_URL = "https://api.clickup.com/api/v2"


def _headers() -> dict:
    return {"Authorization": os.environ["CLICKUP_TOKEN"]}


def get_tasks(team_id: str, user_id: int, page: int = 0) -> list[dict]:
    resp = requests.get(
        f"{BASE_URL}/team/{team_id}/task",
        headers=_headers(),
        params={
            "assignees[]": user_id,
            "include_closed": "true",
            "subtasks": "true",
            "page": page,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("tasks", [])


def get_all_tasks(team_id: str, user_id: int) -> list[dict]:
    tasks, page = [], 0
    while True:
        batch = get_tasks(team_id, user_id, page)
        if not batch:
            break
        tasks.extend(batch)
        page += 1
    return tasks


def get_task_activity(task_id: str) -> list[dict]:
    """
    Returns assignment-change events for a task.
    Each event: {"field": "assignee", "date": <unix_ms_str>, "before": {"id": ...}, "after": {"id": ...}}

    ClickUp API shape varies by plan. If this returns empty for known tasks,
    print resp.json() and find where assignment history lives, then update this function.
    """
    resp = requests.get(
        f"{BASE_URL}/task/{task_id}",
        headers=_headers(),
        params={"include_task_history": "true"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    history = data.get("history") or data.get("activity") or []
    return [e for e in history if e.get("field") == "assignee"]
```

- [ ] **Step 2: Smoke-test against real API**

Create a temporary file `_smoke.py` (do NOT commit):
```python
import os
from dotenv import load_dotenv
load_dotenv()
import clickup_client

tasks = clickup_client.get_all_tasks(os.environ["CLICKUP_TEAM_ID"], int(os.environ["CLICKUP_USER_ID"]))
print(f"Fetched {len(tasks)} tasks")
if tasks:
    t = tasks[0]
    print(f"Sample task: {t['id']} — {t['name']}")
    activity = clickup_client.get_task_activity(t["id"])
    print(f"Activity events: {len(activity)}")
    if activity:
        print(f"Sample event: {activity[0]}")
    else:
        import json; print("Full task response:"); print(json.dumps(t, indent=2))
```

Run:
```bash
python _smoke.py
```

If `activity` is empty and the full task response shows assignment history elsewhere, update `get_task_activity()` to extract from the correct key. Delete `_smoke.py` after.

- [ ] **Step 3: Commit**

```bash
git add clickup_client.py
git commit -m "feat: ClickUp API v2 client"
```

---

## Task 4: metrics.py

**Files:**
- Create: `metrics.py`
- Create: `tests/test_metrics.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_metrics.py
from datetime import datetime, timezone
import pytest
from metrics import calculate_task_metrics, calculate_summary, TaskMetrics

USER_ID = 111
OTHER_ID = 222
OTHER_ID_2 = 333


def ms(dt: datetime) -> str:
    """Convert datetime to Unix millisecond string as ClickUp returns."""
    return str(int(dt.timestamp() * 1000))


def make_event(from_id, to_id, dt: datetime) -> dict:
    return {
        "field": "assignee",
        "date": ms(dt),
        "before": {"id": from_id},
        "after": {"id": to_id},
    }


D = datetime(2026, 5, 1, tzinfo=timezone.utc)  # deadline


def test_on_time_delivery():
    events = [make_event(USER_ID, OTHER_ID, datetime(2026, 4, 29, tzinfo=timezone.utc))]
    result = calculate_task_metrics("t1", "Task", ms(D), events, USER_ID)
    assert result.on_time is True
    assert result.delay_days < 0
    assert result.iterations == 0
    assert result.excluded is False


def test_late_delivery():
    events = [make_event(USER_ID, OTHER_ID, datetime(2026, 5, 3, tzinfo=timezone.utc))]
    result = calculate_task_metrics("t1", "Task", ms(D), events, USER_ID)
    assert result.on_time is False
    assert result.delay_days == pytest.approx(2.0, abs=0.1)
    assert result.iterations == 0


def test_one_iteration():
    events = [
        make_event(USER_ID, OTHER_ID, datetime(2026, 4, 28, tzinfo=timezone.utc)),
        make_event(OTHER_ID, USER_ID, datetime(2026, 4, 29, tzinfo=timezone.utc)),
        make_event(USER_ID, OTHER_ID, datetime(2026, 4, 30, tzinfo=timezone.utc)),
    ]
    result = calculate_task_metrics("t1", "Task", ms(D), events, USER_ID)
    assert result.iterations == 1
    assert result.on_time is True


def test_two_iterations():
    events = [
        make_event(USER_ID, OTHER_ID, datetime(2026, 4, 26, tzinfo=timezone.utc)),
        make_event(OTHER_ID, USER_ID, datetime(2026, 4, 27, tzinfo=timezone.utc)),
        make_event(USER_ID, OTHER_ID, datetime(2026, 4, 28, tzinfo=timezone.utc)),
        make_event(OTHER_ID, USER_ID, datetime(2026, 4, 29, tzinfo=timezone.utc)),
        make_event(USER_ID, OTHER_ID, datetime(2026, 4, 30, tzinfo=timezone.utc)),
    ]
    result = calculate_task_metrics("t1", "Task", ms(D), events, USER_ID)
    assert result.iterations == 2


def test_excluded_no_deadline():
    events = [make_event(USER_ID, OTHER_ID, datetime(2026, 4, 28, tzinfo=timezone.utc))]
    result = calculate_task_metrics("t1", "Task", None, events, USER_ID)
    assert result.excluded is True
    assert result.exclusion_reason == "brak deadline"


def test_excluded_no_handoff():
    events = []
    result = calculate_task_metrics("t1", "Task", ms(D), events, USER_ID)
    assert result.excluded is True
    assert result.exclusion_reason == "brak handoffa"


def test_ignored_events_from_others():
    """Events between two non-user people should not count as handoff."""
    events = [
        make_event(OTHER_ID, OTHER_ID_2, datetime(2026, 4, 25, tzinfo=timezone.utc)),
        make_event(USER_ID, OTHER_ID, datetime(2026, 4, 30, tzinfo=timezone.utc)),
    ]
    result = calculate_task_metrics("t1", "Task", ms(D), events, USER_ID)
    assert result.on_time is True
    assert result.iterations == 0


def test_summary_counts():
    m1 = calculate_task_metrics(
        "t1", "T1", ms(D),
        [make_event(USER_ID, OTHER_ID, datetime(2026, 4, 29, tzinfo=timezone.utc))],
        USER_ID,
    )
    m2 = calculate_task_metrics(
        "t2", "T2", ms(D),
        [make_event(USER_ID, OTHER_ID, datetime(2026, 5, 3, tzinfo=timezone.utc))],
        USER_ID,
    )
    m3 = calculate_task_metrics("t3", "T3", None, [], USER_ID)
    summary = calculate_summary([m1, m2, m3])
    assert summary.total_tasks == 3
    assert summary.excluded_tasks == 1
    assert summary.on_time_count == 1
    assert summary.late_count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_metrics.py -v
```

Expected: `ModuleNotFoundError: No module named 'metrics'`

- [ ] **Step 3: Implement metrics.py**

```python
# metrics.py
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class TaskMetrics:
    task_id: str
    task_name: str
    deadline: Optional[datetime]
    first_handoff: Optional[datetime]
    on_time: Optional[bool]
    delay_days: Optional[float]
    iterations: int
    excluded: bool
    exclusion_reason: Optional[str]


@dataclass
class Summary:
    total_tasks: int
    excluded_tasks: int
    on_time_count: int
    late_count: int
    on_time_pct: float
    avg_delay_days: float
    max_delay_days: float
    avg_iterations: float
    max_iterations: int
    delay_buckets: list[dict]
    iteration_buckets: list[dict]


def _parse_ms(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)


def calculate_task_metrics(
    task_id: str,
    task_name: str,
    due_date_ms: Optional[str],
    events: list[dict],
    user_id: int,
) -> TaskMetrics:
    deadline = _parse_ms(due_date_ms)

    # Sort events chronologically
    sorted_events = sorted(events, key=lambda e: int(e["date"]))

    # Find first handoff: user -> non-user
    first_handoff: Optional[datetime] = None
    for event in sorted_events:
        before_id = (event.get("before") or {}).get("id")
        after_id = (event.get("after") or {}).get("id")
        if before_id == user_id and after_id != user_id:
            first_handoff = _parse_ms(event["date"])
            break

    if not deadline:
        return TaskMetrics(
            task_id=task_id, task_name=task_name, deadline=None,
            first_handoff=first_handoff, on_time=None, delay_days=None,
            iterations=0, excluded=True, exclusion_reason="brak deadline",
        )

    if not first_handoff:
        return TaskMetrics(
            task_id=task_id, task_name=task_name, deadline=deadline,
            first_handoff=None, on_time=None, delay_days=None,
            iterations=0, excluded=True, exclusion_reason="brak handoffa",
        )

    delay_days = (first_handoff - deadline).total_seconds() / 86400
    on_time = first_handoff <= deadline

    # Count iterations: bounces after first handoff
    iterations = 0
    at_user = False
    post_events = [
        e for e in sorted_events
        if int(e["date"]) > int(first_handoff.timestamp() * 1000)
    ]
    for event in post_events:
        after_id = (event.get("after") or {}).get("id")
        before_id = (event.get("before") or {}).get("id")
        if after_id == user_id:
            at_user = True
        elif before_id == user_id and after_id != user_id and at_user:
            iterations += 1
            at_user = False

    return TaskMetrics(
        task_id=task_id, task_name=task_name, deadline=deadline,
        first_handoff=first_handoff, on_time=on_time, delay_days=delay_days,
        iterations=iterations, excluded=False, exclusion_reason=None,
    )


def calculate_summary(metrics: list[TaskMetrics]) -> Summary:
    included = [m for m in metrics if not m.excluded]
    on_time = [m for m in included if m.on_time]
    late = [m for m in included if not m.on_time]

    delays = [m.delay_days for m in late if m.delay_days is not None]
    iterations = [m.iterations for m in included]

    on_time_pct = round(len(on_time) / len(included) * 100, 1) if included else 0.0
    avg_delay = round(sum(delays) / len(delays), 1) if delays else 0.0
    max_delay = round(max(delays), 1) if delays else 0.0
    avg_iter = round(sum(iterations) / len(iterations), 1) if iterations else 0.0
    max_iter = max(iterations) if iterations else 0

    # Delay distribution buckets (days late, only for late tasks)
    delay_buckets = _delay_buckets(delays)
    iter_buckets = _iter_buckets(iterations)

    return Summary(
        total_tasks=len(metrics),
        excluded_tasks=len(metrics) - len(included),
        on_time_count=len(on_time),
        late_count=len(late),
        on_time_pct=on_time_pct,
        avg_delay_days=avg_delay,
        max_delay_days=max_delay,
        avg_iterations=avg_iter,
        max_iterations=max_iter,
        delay_buckets=delay_buckets,
        iter_buckets=iter_buckets,
    )


def _delay_buckets(delays: list[float]) -> list[dict]:
    brackets = [("1d", 0, 1), ("2-3d", 1, 3), ("4-7d", 3, 7), ("8-14d", 7, 14), (">14d", 14, float("inf"))]
    return [
        {"label": label, "count": sum(1 for d in delays if lo < d <= hi)}
        for label, lo, hi in brackets
    ]


def _iter_buckets(iterations: list[int]) -> list[dict]:
    max_shown = max(iterations) if iterations else 0
    buckets = []
    for i in range(0, min(max_shown + 1, 6)):
        buckets.append({"label": str(i), "count": iterations.count(i)})
    if max_shown >= 6:
        buckets.append({"label": "6+", "count": sum(1 for x in iterations if x >= 6)})
    return buckets
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_metrics.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add metrics.py tests/test_metrics.py
git commit -m "feat: metric calculations — terminowość, opóźnienie, iteracje"
```

---

## Task 5: main.py

**Files:**
- Create: `main.py`

- [ ] **Step 1: Implement main.py**

```python
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

TEAM_ID = os.environ["CLICKUP_TEAM_ID"]
USER_ID = int(os.environ["CLICKUP_USER_ID"])


def _fetch_raw() -> dict:
    cached = cache.load_cache()
    if cached:
        return cached

    tasks = clickup_client.get_all_tasks(TEAM_ID, USER_ID)
    activity = {t["id"]: clickup_client.get_task_activity(t["id"]) for t in tasks}
    payload = {"tasks": tasks, "activity": activity}
    cache.save_cache(payload)
    return payload


def _build_metrics(days: int, start: Optional[str], end: Optional[str]) -> tuple[list, m.Summary]:
    raw = _fetch_raw()

    now = datetime.now(tz=timezone.utc)
    if start and end:
        filter_start = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
        filter_end = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
    else:
        filter_start = now - timedelta(days=days)
        filter_end = now

    task_metrics = []
    for task in raw["tasks"]:
        tm = m.calculate_task_metrics(
            task_id=task["id"],
            task_name=task["name"],
            due_date_ms=task.get("due_date"),
            events=raw["activity"].get(task["id"], []),
            user_id=USER_ID,
        )
        # Filter by first_handoff date (or deadline for excluded tasks)
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
```

- [ ] **Step 2: Run app locally to verify it starts**

```bash
uvicorn main:app --reload
```

Expected: `Uvicorn running on http://127.0.0.1:8000`. Open browser — will show Jinja2 template error (not yet created). That's fine.

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "feat: FastAPI app with dashboard and refresh endpoints"
```

---

## Task 6: templates/dashboard.html

**Files:**
- Create: `templates/dashboard.html`

- [ ] **Step 1: Create templates directory**

```bash
mkdir -p templates
```

- [ ] **Step 2: Create dashboard.html**

```html
<!DOCTYPE html>
<html lang="pl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ClickUp Dashboard</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: system-ui, sans-serif; background: #f5f5f5; color: #333; }
    header { background: #fff; padding: 16px 24px; border-bottom: 1px solid #e0e0e0; display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
    header h1 { font-size: 1.2rem; font-weight: 600; }
    .filters { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .filters a { padding: 6px 14px; border-radius: 6px; text-decoration: none; background: #eee; color: #333; font-size: 0.875rem; }
    .filters a.active { background: #7c3aed; color: #fff; }
    .filters input[type=date] { padding: 5px 10px; border: 1px solid #ccc; border-radius: 6px; font-size: 0.875rem; }
    .refresh { margin-left: auto; padding: 6px 14px; background: #7c3aed; color: #fff; border-radius: 6px; text-decoration: none; font-size: 0.875rem; }
    .grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; padding: 24px; }
    @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
    .card { background: #fff; border-radius: 10px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
    .card h2 { font-size: 0.85rem; font-weight: 600; text-transform: uppercase; letter-spacing: .05em; color: #888; margin-bottom: 8px; }
    .stat { font-size: 2rem; font-weight: 700; margin-bottom: 4px; }
    .stat-sub { font-size: 0.85rem; color: #888; margin-bottom: 16px; }
    .chart-wrap { position: relative; height: 180px; }
    table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 10px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.08); margin: 0 24px 24px; width: calc(100% - 48px); }
    th { padding: 12px 16px; text-align: left; font-size: 0.8rem; text-transform: uppercase; letter-spacing: .05em; color: #888; border-bottom: 1px solid #eee; cursor: pointer; user-select: none; }
    th:hover { background: #fafafa; }
    td { padding: 12px 16px; font-size: 0.875rem; border-bottom: 1px solid #f0f0f0; }
    tr:last-child td { border-bottom: none; }
    .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: 600; }
    .badge-ok { background: #d1fae5; color: #065f46; }
    .badge-late { background: #fee2e2; color: #991b1b; }
    .badge-warn { background: #fef3c7; color: #92400e; }
    .excluded { color: #aaa; font-style: italic; }
  </style>
</head>
<body>

<header>
  <h1>ClickUp Dashboard</h1>
  <div class="filters">
    <a href="/?days=30" class="{{ 'active' if days == 30 and not start else '' }}">30 dni</a>
    <a href="/?days=90" class="{{ 'active' if days == 90 and not start else '' }}">90 dni</a>
    <form method="get" action="/" style="display:flex;gap:6px;align-items:center;">
      <input type="date" name="start" value="{{ start }}" required>
      <input type="date" name="end" value="{{ end }}" required>
      <button type="submit" style="padding:6px 12px;background:#eee;border:none;border-radius:6px;cursor:pointer;font-size:.875rem;">Zastosuj</button>
    </form>
  </div>
  <a href="/api/refresh" class="refresh">Odśwież dane</a>
</header>

<div class="grid">
  <!-- Terminowość -->
  <div class="card">
    <h2>Terminowość</h2>
    <div class="stat">{{ summary.on_time_pct }}%</div>
    <div class="stat-sub">{{ summary.on_time_count }} na czas / {{ summary.late_count }} po terminie</div>
    <div class="chart-wrap">
      <canvas id="chartOnTime"></canvas>
    </div>
  </div>

  <!-- Opóźnienie -->
  <div class="card">
    <h2>Opóźnienie</h2>
    <div class="stat">{{ summary.avg_delay_days }}d</div>
    <div class="stat-sub">średnia | maks. {{ summary.max_delay_days }}d</div>
    <div class="chart-wrap">
      <canvas id="chartDelay"></canvas>
    </div>
  </div>

  <!-- Iteracje -->
  <div class="card">
    <h2>Iteracje</h2>
    <div class="stat">{{ summary.avg_iterations }}</div>
    <div class="stat-sub">średnia | maks. {{ summary.max_iterations }}</div>
    <div class="chart-wrap">
      <canvas id="chartIter"></canvas>
    </div>
  </div>
</div>

<!-- Tabela zadań -->
<table id="taskTable">
  <thead>
    <tr>
      <th onclick="sortTable(0)">Zadanie ↕</th>
      <th onclick="sortTable(1)">Deadline ↕</th>
      <th onclick="sortTable(2)">Handoff ↕</th>
      <th onclick="sortTable(3)">Opóźnienie ↕</th>
      <th onclick="sortTable(4)">Iteracje ↕</th>
      <th>Status</th>
    </tr>
  </thead>
  <tbody>
    {% for t in tasks %}
    <tr class="{{ 'excluded' if t.excluded else '' }}">
      <td>{{ t.task_name }}</td>
      <td>{{ t.deadline.strftime('%Y-%m-%d') if t.deadline else '—' }}</td>
      <td>{{ t.first_handoff.strftime('%Y-%m-%d') if t.first_handoff else '—' }}</td>
      <td>
        {% if t.delay_days is not none %}
          {{ '%+.1f'|format(t.delay_days) }}d
        {% else %}—{% endif %}
      </td>
      <td>{{ t.iterations if not t.excluded else '—' }}</td>
      <td>
        {% if t.excluded %}
          <span class="badge badge-warn">⚠ {{ t.exclusion_reason }}</span>
        {% elif t.on_time %}
          <span class="badge badge-ok">Na czas</span>
        {% else %}
          <span class="badge badge-late">Po terminie</span>
        {% endif %}
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>

<script>
const onTimeData = {{ summary.on_time_count }};
const lateData = {{ summary.late_count }};
const delayBuckets = {{ summary.delay_buckets | tojson }};
const iterBuckets = {{ summary.iter_buckets | tojson }};

new Chart(document.getElementById('chartOnTime'), {
  type: 'pie',
  data: {
    labels: ['Na czas', 'Po terminie'],
    datasets: [{ data: [onTimeData, lateData], backgroundColor: ['#34d399', '#f87171'] }]
  },
  options: { plugins: { legend: { position: 'bottom' } }, responsive: true, maintainAspectRatio: false }
});

new Chart(document.getElementById('chartDelay'), {
  type: 'bar',
  data: {
    labels: delayBuckets.map(b => b.label),
    datasets: [{ label: 'Zadania', data: delayBuckets.map(b => b.count), backgroundColor: '#f87171' }]
  },
  options: { plugins: { legend: { display: false } }, responsive: true, maintainAspectRatio: false,
    scales: { y: { beginAtZero: true, ticks: { stepSize: 1 } } } }
});

new Chart(document.getElementById('chartIter'), {
  type: 'bar',
  data: {
    labels: iterBuckets.map(b => b.label),
    datasets: [{ label: 'Zadania', data: iterBuckets.map(b => b.count), backgroundColor: '#818cf8' }]
  },
  options: { plugins: { legend: { display: false } }, responsive: true, maintainAspectRatio: false,
    scales: { y: { beginAtZero: true, ticks: { stepSize: 1 } } } }
});

// Sortable table
let sortDir = {};
function sortTable(col) {
  const table = document.getElementById('taskTable');
  const rows = Array.from(table.querySelectorAll('tbody tr'));
  sortDir[col] = !sortDir[col];
  rows.sort((a, b) => {
    const av = a.cells[col].textContent.trim();
    const bv = b.cells[col].textContent.trim();
    const n = parseFloat(av) - parseFloat(bv);
    if (!isNaN(n)) return sortDir[col] ? n : -n;
    return sortDir[col] ? av.localeCompare(bv) : bv.localeCompare(av);
  });
  rows.forEach(r => table.querySelector('tbody').appendChild(r));
}
</script>

</body>
</html>
```

- [ ] **Step 3: Test full app in browser**

```bash
cp .env.example .env  # fill in real values
uvicorn main:app --reload
```

Open `http://localhost:8000`. Verify:
- Three charts render
- Table shows tasks
- 30d / 90d filter links work
- "Odśwież dane" clears cache and reloads

- [ ] **Step 4: Run full test suite**

```bash
pytest -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add templates/ main.py
git commit -m "feat: Jinja2 dashboard template with Chart.js"
```

---

## Task 7: Deploy to Render

**Files:**
- Create: `render.yaml`

- [ ] **Step 1: Create render.yaml**

```yaml
services:
  - type: web
    name: clickup-dashboard
    runtime: python
    buildCommand: pip install -r requirements.txt
    startCommand: uvicorn main:app --host 0.0.0.0 --port $PORT
    envVars:
      - key: CLICKUP_TOKEN
        sync: false
      - key: CLICKUP_USER_ID
        sync: false
      - key: CLICKUP_TEAM_ID
        sync: false
```

- [ ] **Step 2: Push to GitHub**

```bash
git remote add origin <your-github-repo-url>
git push -u origin master
```

- [ ] **Step 3: Deploy on Render**

1. Go to https://render.com → New → Web Service
2. Connect GitHub repo
3. Render auto-detects `render.yaml`
4. In Environment → add three variables: `CLICKUP_TOKEN`, `CLICKUP_USER_ID`, `CLICKUP_TEAM_ID`
5. Click Deploy

- [ ] **Step 4: Verify live URL**

Open the Render-provided URL. Verify dashboard loads and data appears.

- [ ] **Step 5: Final commit**

```bash
git add render.yaml
git commit -m "chore: Render deploy config"
git push
```

---

## Notes

- **Cache persistence on Render:** Render's free tier has an ephemeral filesystem — `cache.json` resets on every deploy/restart. This is acceptable: the app fetches fresh data on restart, then caches in-memory for 15 min per session. If persistence becomes important, replace `cache.py` with a Redis URL (Render provides one).
- **Rate limits:** ClickUp API allows 100 req/min. If you have hundreds of tasks, the initial load (one request per task for activity) may hit the limit. Add `time.sleep(0.6)` between activity requests in `clickup_client.get_task_activity()` if you see 429 errors.
- **`tojson` filter:** Jinja2's `tojson` is available by default in FastAPI's Jinja2Templates.
