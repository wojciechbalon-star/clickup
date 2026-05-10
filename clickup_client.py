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
