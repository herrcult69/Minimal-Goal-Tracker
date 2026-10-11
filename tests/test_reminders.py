"""Reminder scheduling with an injected wall clock and RNG (§11.1).

No event loop: the scheduler's timer is real but never fires in tests — each
wake is simulated by advancing the fake clock and calling ``_on_wake()``.
"""

import random
from datetime import datetime, timedelta

import pytest
from PyQt6.QtCore import QCoreApplication

from mgt.reminders import (
    DEFER_S,
    GRACE_S,
    MAX_WAKE_S,
    QUOTES,
    WINDOWS,
    ReminderScheduler,
)

BASE = datetime(2026, 10, 8, 9, 0, 0).timestamp()  # 09:00 local, before window 1


@pytest.fixture(scope="module", autouse=True)
def qapp():
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


class FakeClock:
    def __init__(self, start: float = BASE) -> None:
        self.t = start

    def now(self) -> float:
        return self.t


class FakeNotifier:
    def __init__(self) -> None:
        self.calls = []

    def notify(self, title: str, body: str) -> bool:
        self.calls.append((title, body))
        return True


class FakePomodoro:
    def __init__(self, running=False, mode="focus") -> None:
        self.is_running = running
        self.current_mode = mode


@pytest.fixture()
def clock():
    return FakeClock()


@pytest.fixture()
def notifier():
    return FakeNotifier()


def build(clock, notifier, pomodoro=None, top_title=None):
    return ReminderScheduler(
        notifier,
        pomodoro=pomodoro,
        top_title=top_title,
        now=clock.now,
        rng=random.Random(42),
    )


def test_three_due_times_inside_windows(clock, notifier):
    sched = build(clock, notifier)
    due = sched.due_times
    assert len(due) == 3
    assert due == sorted(due)
    for ts, (start_h, end_h) in zip(due, WINDOWS):
        hour = datetime.fromtimestamp(ts).hour
        assert start_h <= hour < end_h


def test_arm_is_capped_at_fifteen_minutes(clock, notifier):
    # 09:00 -> next due is at least 10:00, so the wait is clamped to 15 min.
    sched = build(clock, notifier)
    assert sched._timer_instance().interval() == MAX_WAKE_S * 1000


def test_wake_at_due_fires_and_advances(clock, notifier):
    sched = build(clock, notifier, top_title=lambda: "Ship report")
    first = sched.due_times[0]
    clock.t = first
    sched._on_wake()
    assert notifier.calls == [
        ("Minimalist Goal Tracker", "Focus Check: Have you worked on 'Ship report' today?")
    ]
    assert len(sched.due_times) == 2
    assert sched.due_times[0] > first


def test_late_wake_within_grace_still_fires(clock, notifier):
    sched = build(clock, notifier, top_title=lambda: "Task")
    clock.t = sched.due_times[0] + GRACE_S - 1
    sched._on_wake()
    assert len(notifier.calls) == 1


def test_missed_wake_is_skipped_not_delivered_late(clock, notifier):
    sched = build(clock, notifier, top_title=lambda: "Task")
    clock.t = sched.due_times[0] + GRACE_S + 1
    sched._on_wake()
    assert notifier.calls == []
    assert len(sched.due_times) == 2


def test_focus_session_defers_by_ten_minutes(clock, notifier):
    sched = build(clock, notifier, pomodoro=FakePomodoro(running=True, mode="focus"))
    first = sched.due_times[0]
    clock.t = first
    sched._on_wake()
    assert notifier.calls == []
    assert len(sched.due_times) == 3
    assert sched.due_times[0] == pytest.approx(first + DEFER_S)


def test_break_session_does_not_defer(clock, notifier):
    sched = build(clock, notifier, pomodoro=FakePomodoro(running=True, mode="break"),
                  top_title=lambda: "Task")
    clock.t = sched.due_times[0]
    sched._on_wake()
    assert len(notifier.calls) == 1


def test_exhausted_day_builds_tomorrow(clock, notifier):
    sched = build(clock, notifier, top_title=lambda: "Task")
    clock.t = sched.due_times[2] + GRACE_S + 1
    sched._on_wake()
    assert notifier.calls == []
    due = sched.due_times
    assert len(due) == 3
    tomorrow = datetime.fromtimestamp(clock.t).date() + timedelta(days=1)
    assert all(datetime.fromtimestamp(ts).date() == tomorrow for ts in due)


def test_body_falls_back_to_a_quote(clock, notifier):
    sched = build(clock, notifier, top_title=lambda: None)
    clock.t = sched.due_times[0]
    sched._on_wake()
    assert notifier.calls[0][1] in QUOTES


def test_broken_top_title_provider_is_tolerated(clock, notifier):
    def boom():
        raise RuntimeError("db gone")

    sched = build(clock, notifier, top_title=boom)
    clock.t = sched.due_times[0]
    sched._on_wake()
    assert notifier.calls[0][1] in QUOTES
