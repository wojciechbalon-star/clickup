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
