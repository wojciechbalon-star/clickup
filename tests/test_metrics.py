from datetime import datetime, timezone
import pytest
from metrics import calculate_task_metrics, calculate_summary


def ms(dt: datetime) -> str:
    return str(int(dt.timestamp() * 1000))


D = datetime(2026, 5, 1, tzinfo=timezone.utc)  # deadline


def test_on_time_delivery():
    result = calculate_task_metrics("t1", "Task", ms(D), ms(datetime(2026, 4, 29, tzinfo=timezone.utc)))
    assert result.on_time is True
    assert result.delay_days < 0
    assert result.excluded is False


def test_late_delivery():
    result = calculate_task_metrics("t1", "Task", ms(D), ms(datetime(2026, 5, 3, tzinfo=timezone.utc)))
    assert result.on_time is False
    assert result.delay_days == pytest.approx(2.0, abs=0.1)


def test_excluded_no_deadline():
    result = calculate_task_metrics("t1", "Task", None, ms(datetime(2026, 4, 28, tzinfo=timezone.utc)))
    assert result.excluded is True
    assert result.exclusion_reason == "brak deadline"


def test_excluded_no_handoff():
    result = calculate_task_metrics("t1", "Task", ms(D), None)
    assert result.excluded is True
    assert result.exclusion_reason == "brak handoffa"


def test_excluded_both_none():
    result = calculate_task_metrics("t1", "Task", None, None)
    assert result.excluded is True


def test_summary_counts():
    m1 = calculate_task_metrics("t1", "T1", ms(D), ms(datetime(2026, 4, 29, tzinfo=timezone.utc)))
    m2 = calculate_task_metrics("t2", "T2", ms(D), ms(datetime(2026, 5, 3, tzinfo=timezone.utc)))
    m3 = calculate_task_metrics("t3", "T3", None, None)
    summary = calculate_summary([m1, m2, m3])
    assert summary.total_tasks == 3
    assert summary.excluded_tasks == 1
    assert summary.on_time_count == 1
    assert summary.late_count == 1
    assert summary.on_time_pct == 50.0
