import os
import requests
from typing import Optional

BASE_URL = "https://api.clickup.com/api/v2"
HANDOFF_STATUS = "internal review"
DEADLINE_FIELD_ID = "57f7db78-056c-4a9c-94f9-d19db5576f30"


def _headers() -> dict:
    return {"Authorization": os.environ["CLICKUP_TOKEN"]}


def get_tasks(team_id: str, user_id: int, page: int = 0) -> list[dict]:
    resp = requests.get(
        f"{BASE_URL}/team/{team_id}/task",
        headers=_headers(),
        params={
            "assignees[]": str(user_id),
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


def get_deadline_ms(task: dict) -> Optional[str]:
    """Extract deadline from custom field 'Deadline [all]'."""
    for cf in task.get("custom_fields", []):
        if cf.get("id") == DEADLINE_FIELD_ID:
            value = cf.get("value")
            return str(value) if value is not None else None
    return None


def register_webhook(team_id: str, endpoint_url: str) -> dict:
    resp = requests.post(
        f"{BASE_URL}/team/{team_id}/webhook",
        headers=_headers(),
        json={"endpoint": endpoint_url, "events": ["taskAssigneeUpdated"]},
        timeout=15,
    )
    return resp.json()


def list_webhooks(team_id: str) -> dict:
    resp = requests.get(
        f"{BASE_URL}/team/{team_id}/webhook",
        headers=_headers(),
        timeout=15,
    )
    return resp.json()


def get_handoff_ms(task: dict) -> Optional[str]:
    """
    Returns Unix ms timestamp (as str) of the first handoff event.
    Priority: first 'internal review' entry from status history, then date_closed.
    """
    task_id = task["id"]
    resp = requests.get(
        f"{BASE_URL}/task/{task_id}/time_in_status",
        headers=_headers(),
        timeout=15,
    )
    if resp.ok:
        data = resp.json()
        current = data.get("current_status", {})
        if current.get("status", "").lower() == HANDOFF_STATUS:
            ts = current.get("total_time", {}).get("since")
            if ts:
                return str(ts)

        for entry in data.get("status_history", []):
            if entry.get("status", "").lower() == HANDOFF_STATUS:
                ts = entry.get("total_time", {}).get("since")
                if ts:
                    return str(ts)

    # Fallback: task was completed without going through internal review
    date_closed = task.get("date_closed")
    if date_closed:
        return str(date_closed)

    return None
