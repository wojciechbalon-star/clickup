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
    iter_buckets: list[dict]


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
        delay_buckets=_delay_buckets(delays),
        iter_buckets=_iter_buckets(iterations),
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
