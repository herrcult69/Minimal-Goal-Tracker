"""Pomodoro tick/complete/pause logic, driven by a fake monotonic clock.

No event loop is needed: the controller's timer is real, but the tests advance
the injected clock and call ``_on_timeout()`` to simulate each wake exactly.
"""

import pytest
from PyQt6.QtCore import QCoreApplication

from mgt.pomodoro import BREAK, FOCUS, PomodoroController, format_seconds


@pytest.fixture(scope="module", autouse=True)
def qapp():
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture()
def clock():
    return FakeClock()


@pytest.fixture()
def ctl(clock):
    return PomodoroController(focus_minutes=25, break_minutes=5, now=clock.now)


class Recorder:
    """Collects signal emissions so tests can assert on the exact stream."""

    def __init__(self, controller):
        self.ticks = []
        self.states = []
        self.completed = []
        controller.tick.connect(lambda text, secs: self.ticks.append((text, secs)))
        controller.state_changed.connect(
            lambda running, mode: self.states.append((running, mode))
        )
        controller.completed.connect(self.completed.append)


# --- basics ---------------------------------------------------------------

def test_format_seconds():
    assert format_seconds(0) == "00:00"
    assert format_seconds(59) == "00:59"
    assert format_seconds(60) == "01:00"
    assert format_seconds(1500) == "25:00"
    assert format_seconds(7200) == "120:00"


def test_initial_state(ctl):
    assert ctl.current_mode == FOCUS
    assert ctl.is_running is False
    assert ctl.remaining_seconds == 1500
    assert ctl.focus_duration_minutes == 25
    assert ctl.break_duration_minutes == 5


def test_durations_are_clamped():
    c = PomodoroController(focus_minutes=0, break_minutes=999, now=lambda: 0.0)
    assert c.focus_duration_minutes == 1
    assert c.break_duration_minutes == 60


# --- start / pause / toggle ----------------------------------------------

def test_start_emits_state_then_tick(ctl, clock):
    rec = Recorder(ctl)
    ctl.start()
    assert ctl.is_running is True
    assert rec.states == [(True, FOCUS)]
    assert rec.ticks == [("25:00", 1500)]


def test_start_is_idempotent(ctl):
    rec = Recorder(ctl)
    ctl.start()
    ctl.start()
    assert rec.states == [(True, FOCUS)]


def test_pause_freezes_remaining(ctl, clock):
    rec = Recorder(ctl)
    ctl.start()
    clock.advance(10.0)
    ctl.pause()
    assert ctl.is_running is False
    assert ctl.remaining_seconds == 1490
    assert rec.states[-1] == (False, FOCUS)
    clock.advance(100.0)  # paused time must not count down
    assert ctl.remaining_seconds == 1490


def test_pause_is_noop_when_idle(ctl):
    rec = Recorder(ctl)
    ctl.pause()
    assert rec.states == []
    assert ctl.remaining_seconds == 1500


def test_resume_continues_from_frozen_remaining(ctl, clock):
    rec = Recorder(ctl)
    ctl.start()
    clock.advance(10.0)
    ctl.pause()
    clock.advance(100.0)
    ctl.start()
    clock.advance(1.0)
    ctl._on_timeout()
    assert rec.ticks[-1] == ("24:49", 1489)


def test_toggle_round_trip(ctl):
    ctl.toggle()
    assert ctl.is_running is True
    ctl.toggle()
    assert ctl.is_running is False


# --- ticking --------------------------------------------------------------

def test_tick_only_when_display_changes(ctl, clock):
    rec = Recorder(ctl)
    ctl.start()
    rec.ticks.clear()
    clock.advance(0.2)
    ctl._on_timeout()  # early wake, same displayed second
    assert rec.ticks == []
    clock.advance(0.8)  # now a full second has passed
    ctl._on_timeout()
    assert rec.ticks == [("24:59", 1499)]


def test_remaining_seconds_is_ceiling(ctl, clock):
    ctl.start()
    clock.advance(1.5)
    assert ctl.remaining_seconds == 1499  # ceil(1498.5)


def test_arm_interval_targets_next_display_change(ctl, clock):
    ctl.start()  # remaining 1500.0 -> 1000 ms to the boundary + 2
    assert ctl._timer.interval() == 1002
    clock.advance(1.0)
    ctl._on_timeout()
    assert ctl._timer.interval() == 1002


def test_arm_interval_handles_fractional_remaining(ctl, clock):
    ctl.start()
    clock.advance(0.5)
    ctl.pause()  # remaining 1499.5
    ctl.start()  # ceil = 1500 -> (1499.5 - 1499) * 1000 + 2
    assert ctl._timer.interval() == 502


# --- completion -----------------------------------------------------------

def test_focus_completion_switches_to_break_without_autostart(ctl, clock):
    rec = Recorder(ctl)
    ctl.start()
    clock.advance(1500.0)
    ctl._on_timeout()
    assert rec.completed == [FOCUS]
    assert ctl.current_mode == BREAK
    assert ctl.is_running is False
    assert ctl.remaining_seconds == 300
    assert rec.ticks[-1] == ("05:00", 300)
    assert rec.states[-1] == (False, BREAK)


def test_break_completion_returns_to_focus(ctl, clock):
    rec = Recorder(ctl)
    ctl.start()
    clock.advance(1500.0)
    ctl._on_timeout()
    ctl.start()
    clock.advance(300.0)
    ctl._on_timeout()
    assert rec.completed == [FOCUS, BREAK]
    assert ctl.current_mode == FOCUS
    assert ctl.remaining_seconds == 1500


# --- auto-cycle -----------------------------------------------------------

@pytest.fixture()
def auto_ctl(clock):
    return PomodoroController(
        focus_minutes=25, break_minutes=5, now=clock.now, auto_start=True
    )


def test_auto_start_defaults_off(ctl):
    assert ctl.auto_start is False


def test_auto_start_property_setter(clock):
    c = PomodoroController(now=clock.now)
    assert c.auto_start is False
    c.auto_start = True
    assert c.auto_start is True
    c.auto_start = 0
    assert c.auto_start is False


def test_focus_completion_autostarts_break(auto_ctl, clock):
    rec = Recorder(auto_ctl)
    auto_ctl.start()
    clock.advance(1500.0)
    auto_ctl._on_timeout()
    assert rec.completed == [FOCUS]
    assert auto_ctl.current_mode == BREAK
    assert auto_ctl.is_running is True            # rolled straight into break
    assert auto_ctl.remaining_seconds == 300
    assert rec.ticks[-1] == ("05:00", 300)
    # idle-state flip, then the immediate start of the next phase
    assert rec.states == [(True, FOCUS), (False, BREAK), (True, BREAK)]


def test_break_completion_autostarts_focus(auto_ctl, clock):
    rec = Recorder(auto_ctl)
    auto_ctl.start()
    clock.advance(1500.0)
    auto_ctl._on_timeout()                        # focus -> break (started)
    clock.advance(300.0)
    auto_ctl._on_timeout()                        # break -> focus (started)
    assert rec.completed == [FOCUS, BREAK]
    assert auto_ctl.current_mode == FOCUS
    assert auto_ctl.is_running is True
    assert auto_ctl.remaining_seconds == 1500
    assert rec.states[-1] == (True, FOCUS)


def test_auto_start_can_be_flipped_off_mid_run(auto_ctl, clock):
    rec = Recorder(auto_ctl)
    auto_ctl.auto_start = False
    auto_ctl.start()
    clock.advance(1500.0)
    auto_ctl._on_timeout()
    assert rec.completed == [FOCUS]
    assert auto_ctl.is_running is False           # halts like a non-auto controller


# --- reset ----------------------------------------------------------------

def test_reset_refills_current_phase(ctl, clock):
    rec = Recorder(ctl)
    ctl.start()
    clock.advance(10.0)
    ctl.reset()
    assert ctl.is_running is False
    assert ctl.current_mode == FOCUS
    assert ctl.remaining_seconds == 1500
    assert rec.states[-1] == (False, FOCUS)
    assert rec.ticks[-1] == ("25:00", 1500)


def test_reset_keeps_break_mode(ctl, clock):
    ctl.start()
    clock.advance(1500.0)
    ctl._on_timeout()  # now in break
    ctl.reset()
    assert ctl.current_mode == BREAK
    assert ctl.remaining_seconds == 300


# --- duration updates -----------------------------------------------------

def test_update_durations_while_idle_refills(ctl):
    rec = Recorder(ctl)
    ctl.update_durations(50, 10)
    assert ctl.focus_duration_minutes == 50
    assert ctl.break_duration_minutes == 10
    assert ctl.remaining_seconds == 3000
    assert rec.ticks[-1] == ("50:00", 3000)


def test_update_durations_while_running_only_affects_next_phase(ctl, clock):
    ctl.start()
    ctl.update_durations(50, 10)
    assert ctl.focus_duration_minutes == 50
    assert ctl.remaining_seconds == 1500  # current phase unchanged
    clock.advance(1500.0)
    ctl._on_timeout()
    assert ctl.current_mode == BREAK
    assert ctl.remaining_seconds == 600  # next phase uses the new duration


def test_update_durations_mid_phase_keeps_remaining(ctl, clock):
    ctl.start()
    clock.advance(10.0)
    ctl.pause()
    ctl.update_durations(50, 10)
    assert ctl.remaining_seconds == 1490


def test_update_durations_clamps(ctl):
    ctl.update_durations(0, 999)
    assert ctl.focus_duration_minutes == 1
    assert ctl.break_duration_minutes == 60
