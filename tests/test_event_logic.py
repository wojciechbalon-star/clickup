"""Tests for the assignee-event decision function.

The decision logic is the heart of iteration counting; keep these tight so a
refactor doesn't silently start over- or under-counting iterations.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "postgresql://x:y@localhost/x")

# decide_event_action is pure — pull it out without touching the pool.
from db import decide_event_action  # noqa: E402


USER = 100
OTHER = 200
OTHER2 = 300


def test_uninvolved_event_is_noop():
    assert decide_event_action(OTHER, OTHER2, USER, False) == "noop"
    assert decide_event_action(OTHER, OTHER2, USER, True) == "noop"


def test_user_to_other_first_time_is_handoff():
    assert decide_event_action(USER, OTHER, USER, handoff_done=False) == "first_handoff"


def test_user_to_other_when_already_handed_off_is_noop():
    """Second time the user hands off the same task — don't double-mark."""
    assert decide_event_action(USER, OTHER, USER, handoff_done=True) == "noop"


def test_other_to_user_after_handoff_is_iteration():
    assert decide_event_action(OTHER, USER, USER, handoff_done=True) == "iteration"


def test_other_to_user_without_prior_handoff_is_noop():
    """User gets newly assigned a task without ever having handed it off."""
    assert decide_event_action(OTHER, USER, USER, handoff_done=False) == "noop"


def test_none_before_means_initial_assignment():
    assert decide_event_action(None, USER, USER, handoff_done=False) == "noop"
    assert decide_event_action(None, USER, USER, handoff_done=True) == "iteration"


def test_none_after_means_unassigned():
    assert decide_event_action(USER, None, USER, handoff_done=False) == "first_handoff"
    assert decide_event_action(USER, None, USER, handoff_done=True) == "noop"
