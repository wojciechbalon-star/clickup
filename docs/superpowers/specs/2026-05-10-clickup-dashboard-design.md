# ClickUp Developer Dashboard — Design Spec

**Date:** 2026-05-10  
**Status:** Approved

---

## Problem

A developer needs to show their supervisor measurable progress metrics pulled from ClickUp. Three metrics matter: on-time delivery, delay size, and how many times a task bounced between the developer and others before it was accepted.

---

## Architecture

```
ClickUp API
    ↓
clickup_client.py   — API wrapper, auth, rate limiting
    ↓
metrics.py          — pure metric calculation logic
    ↓
cache.py            — JSON file cache, 15-minute TTL
    ↓
main.py (FastAPI)   — serves HTML, exposes /api/refresh
    ↓
templates/dashboard.html + Chart.js (CDN)
```

Five modules, no database. Deploy on Render as a Web Service.

---

## Configuration

Two environment variables:

| Variable | Description |
|----------|-------------|
| `CLICKUP_TOKEN` | Personal API token |
| `CLICKUP_USER_ID` | Numeric user ID — defines "me" for all metric calculations |
| `CLICKUP_TEAM_ID` | Workspace (team) ID — scope for task queries |

---

## Data Sources

**Tasks:** `GET /team/{team_id}/task`  
Params: `assignees[]={user_id}`, `include_closed=true`, `subtasks=true`

**Activity per task:** `GET /task/{task_id}/activity`  
Used to reconstruct reassignment history in chronological order.

Tasks without a deadline or without a handoff event appear in the table with a warning flag but are excluded from metric calculations.

---

## Metrics

All three metrics are anchored to a single event: **the first handoff** — the first reassignment of the task from the user to anyone else.

### 1. Terminowość (On-Time Delivery)

- **On Time:** `first_handoff_date <= deadline`
- **Late:** `first_handoff_date > deadline`
- Displayed as a pie chart (% and count of On Time / Late).

### 2. Opóźnienie (Delay)

- `opóźnienie = first_handoff_date − deadline` in days
- Negative = delivered early, positive = delivered late
- Calculated only for tasks with both a deadline and a handoff.
- Displayed as a bar chart (distribution), with avg and max shown as summary stats.

### 3. Iteracje (Bounce Count)

- Counts how many times the task returned to the user after the first handoff.
- Each full cycle (back to user → handed off again) = +1 iteration.
- First handoff = 0 iterations. One return and re-handoff = 1 iteration.
- Displayed as a bar chart (distribution), with avg and max shown as summary stats.

### Historical data

ClickUp stores full assignment history, so metrics work retroactively. If a deadline is set today on an old task, the tool compares it against the historical handoff date — this is intentional and correct.

---

## UI

Single page, four sections:

```
┌─────────────────────────────────────────────────┐
│  ClickUp Dashboard   [30d | 90d | custom] [Odśwież] │
├───────────────┬────────────────┬─────────────────┤
│ TERMINOWOŚĆ   │  OPÓŹNIENIE    │   ITERACJE      │
│  pie chart    │  bar chart     │  bar chart      │
│  On Time 72%  │  avg +2.3d     │  avg 1.4        │
│  Late 28%     │  max +12d      │  max 5          │
├───────────────┴────────────────┴─────────────────┤
│  TABELA ZADAŃ                                    │
│  Task | Deadline | Handoff | Opóźnienie | Iter.  │
│  ...  |          |         |            |        │
└─────────────────────────────────────────────────┘
```

- Date filter: buttons `30d / 90d / custom` — applied server-side before metric calculation
- Table sortable by any column
- Tasks excluded from metrics show `⚠ brak deadline` or `⚠ brak handoffa`

---

## Caching

- On first load (or after `/api/refresh`): fetch all tasks + activity from ClickUp API, write to `cache.json`
- On subsequent loads within 15 minutes: read from `cache.json`
- Cache stores raw API responses; metrics are recalculated on every render

---

## Deployment

- Platform: Render Web Service (free tier)
- Runtime: Python 3.12
- Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Environment variables set in Render dashboard: `CLICKUP_TOKEN`, `CLICKUP_USER_ID`
