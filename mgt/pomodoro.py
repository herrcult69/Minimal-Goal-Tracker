"""Headless Pomodoro controller shared by the main window and the overlay.

Deadline-based on a monotonic clock: remaining time is always derived from an
end timestamp, never decremented, so a lagging or suspended machine still shows
the correct value. A single single-shot timer exists **only** while running, and
it is re-armed to land just after each displayed second changes, so there is no
accumulated drift and exactly one wake per second.

The clock is injectable (``now``) so the logic is testable without an event loop.
"""

from __future__ import annotations

import math
import time
from typing import Callable, Optional

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal

FOCUS = "focus"
BREAK = "break"

_MAX_FOCUS_MIN = 120
_MAX_BREAK_MIN = 60


def format_seconds(seconds: int) -> str:
    """``MM:SS`` for a non-negative second count (minutes may exceed 60)."""
    minutes, secs = divmod(max(0, int(seconds)), 60)
    return f"{minutes:02d}:{secs:02d}"


def _clamp_minutes(value: int, maximum: int) -> int:
    return max(1, min(int(value), maximum))


class PomodoroController(QObject):
    """Focus/break timer with pause, reset, and injectable durations."""

    tick = pyqtSignal(str, int)          # (MM:SS, remaining seconds)
    state_changed = pyqtSignal(bool, str)  # (is_running, mode)
    completed = pyqtSignal(str)          # finished mode ("focus" | "break")

    def __init__(
        self,
        focus_minutes: int = 25,
        break_minutes: int = 5,
        parent: Optional[QObject] = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(parent)
        self._now = now
        self._focus_s = _clamp_minutes(focus_minutes, _MAX_FOCUS_MIN) * 60
        self._break_s = _clamp_minutes(break_minutes, _MAX_BREAK_MIN) * 60
        self._mode = FOCUS
        self._running = False
        self._remaining = float(self._focus_s)  # seconds while idle or paused
        self._end = 0.0                          # monotonic deadline while running
        self._last_text = ""                     # last emitted display string
        self._timer: Optional[QTimer] = None

    # --- read-only properties --------------------------------------------

    @property
    def focus_duration_minutes(self) -> int:
        return self._focus_s // 60

    @property
    def break_duration_minutes(self) -> int:
        return self._break_s // 60

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def current_mode(self) -> str:
        return self._mode

    @property
    def remaining_seconds(self) -> int:
        return self._display_seconds()

    # --- control ----------------------------------------------------------

    def start(self) -> None:
        """Resume (or begin) the current phase. No-op when already running."""
        if self._running:
            return
        self._end = self._now() + self._remaining
        self._running = True
        self.state_changed.emit(True, self._mode)
        self._emit_tick()
        self._arm()

    def pause(self) -> None:
        """Freeze the phase, keeping the exact remaining time. No-op when idle."""
        if not self._running:
            return
        self._remaining = max(0.0, self._end - self._now())
        self._running = False
        self._stop_timer()
        self.state_changed.emit(False, self._mode)

    def toggle(self) -> None:
        if self._running:
            self.pause()
        else:
            self.start()

    def reset(self) -> None:
        """Stop and refill the *current* phase. Does not change the mode."""
        was_running = self._running
        self._running = False
        self._stop_timer()
        self._remaining = float(self._duration(self._mode))
        if was_running:
            self.state_changed.emit(False, self._mode)
        self._emit_tick()

    def update_durations(self, focus_minutes: int, break_minutes: int) -> None:
        """Adopt new durations.

        While idle at a full phase the current remaining is refilled; while
        running (or mid-phase) the change only affects the next phase.
        """
        old_full = self._duration(self._mode)
        self._focus_s = _clamp_minutes(focus_minutes, _MAX_FOCUS_MIN) * 60
        self._break_s = _clamp_minutes(break_minutes, _MAX_BREAK_MIN) * 60
        if not self._running and abs(self._remaining - old_full) < 1e-6:
            self._remaining = float(self._duration(self._mode))
            self._emit_tick()

    # --- internals --------------------------------------------------------

    def _duration(self, mode: str) -> int:
        return self._break_s if mode == BREAK else self._focus_s

    def _display_seconds(self) -> int:
        remaining = self._end - self._now() if self._running else self._remaining
        return int(math.ceil(max(0.0, remaining)))

    def _emit_tick(self, force: bool = False) -> None:
        seconds = self._display_seconds()
        text = format_seconds(seconds)
        if force or text != self._last_text:
            self._last_text = text
            self.tick.emit(text, seconds)

    def _timer_instance(self) -> QTimer:
        if self._timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setTimerType(Qt.TimerType.CoarseTimer)
            timer.timeout.connect(self._on_timeout)
            self._timer = timer
        return self._timer

    def _arm(self) -> None:
        """Arm one wake landing just after the next displayed second changes."""
        remaining = max(0.0, self._end - self._now())
        shown = int(math.ceil(remaining))
        ms = max(1, int((remaining - (shown - 1)) * 1000)) + 2
        self._timer_instance().start(ms)

    def _stop_timer(self) -> None:
        if self._timer is not None:
            self._timer.stop()

    def _on_timeout(self) -> None:
        if not self._running:
            return
        if self._end - self._now() <= 0:
            self._complete()
            return
        self._emit_tick()
        self._arm()

    def _complete(self) -> None:
        finished = self._mode
        self._running = False
        self._stop_timer()
        self._mode = BREAK if finished == FOCUS else FOCUS
        self._remaining = float(self._duration(self._mode))
        self._emit_tick(force=True)          # show the new phase's full time
        self.state_changed.emit(False, self._mode)
        self.completed.emit(finished)        # do NOT auto-start the next phase
