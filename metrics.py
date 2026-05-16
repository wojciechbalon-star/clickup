from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class TaskMetrics:
    task_id: str
    task_name: str
    deadline: Optional[datetime]
    first_handoff: Optional[datetime]
    date_created: Optional[datetime]
    on_time: Optional[bool]
    delay_days: Optional[float]
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
    delay_buckets: list[dict]


def _parse_ms(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (ValueError, OSError):
        return None


def calculate_task_metrics(
    task_id: str,
    task_name: str,
    deadline_ms: Optional[str],
    handoff_ms: Optional[str],
    date_created_ms: Optional[str] = None,
) -> TaskMetrics:
    deadline = _parse_ms(deadline_ms)
    first_handoff = _parse_ms(handoff_ms)
    date_created = _parse_ms(date_created_ms)

    if not deadline:
        return TaskMetrics(
            task_id=task_id, task_name=task_name, deadline=None,
            first_handoff=first_handoff, date_created=date_created,
            on_time=None, delay_days=None,
            excluded=True, exclusion_reason="brak deadline",
        )

    if not first_handoff:
        return TaskMetrics(
            task_id=task_id, task_name=task_name, deadline=deadline,
            first_handoff=None, date_created=date_created,
            on_time=None, delay_days=None,
            excluded=True, exclusion_reason="brak handoffa",
        )

    delay_days = (first_handoff - deadline).total_seconds() / 86400
    on_time = first_handoff <= deadline

    return TaskMetrics(
        task_id=task_id, task_name=task_name, deadline=deadline,
        first_handoff=first_handoff, date_created=date_created,
        on_time=on_time, delay_days=delay_days,
        excluded=False, exclusion_reason=None,
    )


def calculate_summary(metrics: list[TaskMetrics]) -> Summary:
    included = [m for m in metrics if not m.excluded]
    on_time = [m for m in included if m.on_time]
    late = [m for m in included if not m.on_time]
    delays = [m.delay_days for m in late if m.delay_days is not None]

    on_time_pct = round(len(on_time) / len(included) * 100, 1) if included else 0.0
    avg_delay = round(sum(delays) / len(delays), 1) if delays else 0.0
    max_delay = round(max(delays), 1) if delays else 0.0

    return Summary(
        total_tasks=len(metrics),
        excluded_tasks=len(metrics) - len(included),
        on_time_count=len(on_time),
        late_count=len(late),
        on_time_pct=on_time_pct,
        avg_delay_days=avg_delay,
        max_delay_days=max_delay,
        delay_buckets=_delay_buckets(delays),
    )


def _delay_buckets(delays: list[float]) -> list[dict]:
    brackets = [("1d", 0, 1), ("2-3d", 1, 3), ("4-7d", 3, 7), ("8-14d", 7, 14), (">14d", 14, float("inf"))]
    return [
        {"label": label, "count": sum(1 for d in delays if lo < d <= hi)}
        for label, lo, hi in brackets
    ]
