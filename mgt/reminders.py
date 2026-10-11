"""Zero-polling random check-in reminders (IMPLEMENTATION.md §11.1).

Three due times per day — one random minute inside each of ``WINDOWS`` — are
picked once, sorted, and handed to a **single** single-shot ``VeryCoarseTimer``.
Each arm sleeps ``min(next_due - now, 15 min)`` on the **wall clock**, so the
schedule survives suspend/resume and clock changes while costing at most one
wake every 15 minutes. A reminder missed while suspended (more than 10 minutes
late) is skipped rather than delivered late, and tomorrow's schedule is built
once today's due times are exhausted.

All time and randomness are injected, so the logic is unit-testable without an
event loop.
"""

from __future__ import annotations

import random
import time
from datetime import datetime, timedelta
from typing import Callable, List, Optional, Sequence

from PyQt6.QtCore import QObject, Qt, QTimer

from .notify import TITLE

#: (start_hour, end_hour) windows; a random minute within each, per day.
WINDOWS: Sequence[tuple] = ((10, 12), (14, 16), (19, 21))

#: Never sleep longer than this between checks, so suspend/resume stays correct.
MAX_WAKE_S = 15 * 60

#: Deliver only when at most this late; a larger gap means it was missed.
GRACE_S = 10 * 60

#: A reminder landing during a Focus session is postponed by this much.
DEFER_S = 10 * 60

#: Fallback bodies when there is nothing planned (no network, §11.2).
QUOTES = (
    "Waste no more time arguing what a good man should be. Be one.",
    "You have power over your mind, not outside events. Realize this.",
    "Well begun is half done.",
    "The impediment to action advances action. What stands in the way becomes the way.",
    "It is not that we have a short time to live, but that we waste much of it.",
    "First say to yourself what you would be; then do what you have to do.",
    "Perfection is not attainable, but if we chase it we can catch excellence.",
    "How we spend our days is, of course, how we spend our lives.",
    "Simplicity is the ultimate sophistication.",
    "Concentrate all your thoughts upon the work at hand.",
    "You do not rise to the level of your goals; you fall to the level of your systems.",
    "The best way out is always through.",
    "Do the hard jobs first. The easy jobs will take care of themselves.",
    "Action is the foundational key to all success.",
    "Little by little, one travels far.",
)


class ReminderScheduler(QObject):
    """Schedule and deliver three random focus check-ins per day."""

    def __init__(
        self,
        notifier: object,
        pomodoro: Optional[object] = None,
        top_title: Optional[Callable[[], Optional[str]]] = None,
        parent: Optional[QObject] = None,
        now: Callable[[], float] = time.time,
        rng: Optional[random.Random] = None,
    ) -> None:
        super().__init__(parent)
        self._notifier = notifier
        self._pomodoro = pomodoro
        self._top_title = top_title
        self._now = now
        self._rng = rng if rng is not None else random.Random()
        self._due: List[float] = self._pick_due(datetime.fromtimestamp(now()).date())
        self._timer: Optional[QTimer] = None
        self._arm()

    # --- schedule ---------------------------------------------------------

    def _pick_due(self, day) -> List[float]:
        """One random minute in each window of ``day``, sorted ascending."""
        base = datetime(day.year, day.month, day.day)
        due = []
        for start_h, end_h in WINDOWS:
            minute = self._rng.randrange((end_h - start_h) * 60)
            due.append((base + timedelta(hours=start_h, minutes=minute)).timestamp())
        return sorted(due)

    @property
    def due_times(self) -> List[float]:
        """Remaining due times (epoch seconds) — read-only snapshot for tests."""
        return list(self._due)

    def _refill_if_empty(self) -> None:
        """No due times left today -> build (tomorrow's) schedule (§11.1.3)."""
        if not self._due:
            tomorrow = datetime.fromtimestamp(self._now()).date() + timedelta(days=1)
            self._due = self._pick_due(tomorrow)

    def _arm(self) -> None:
        """Arm one wake: ``min(next_due - now, 15 min)`` on the wall clock."""
        self._refill_if_empty()
        wait = min(self._due[0] - self._now(), MAX_WAKE_S)
        self._timer_instance().start(int(max(1.0, wait) * 1000))

    def _timer_instance(self) -> QTimer:
        if self._timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setTimerType(Qt.TimerType.VeryCoarseTimer)
            timer.timeout.connect(self._on_wake)
            self._timer = timer
        return self._timer

    # --- wake -------------------------------------------------------------

    def _focus_running(self) -> bool:
        pomodoro = self._pomodoro
        if pomodoro is None:
            return False
        try:
            return bool(pomodoro.is_running) and pomodoro.current_mode == "focus"
        except Exception:  # noqa: BLE001 - never let a check-in wake crash the app
            return False

    def _on_wake(self) -> None:
        now = self._now()
        focus = self._focus_running()
        while self._due and now >= self._due[0]:
            due = self._due[0]
            if now - due > GRACE_S:
                self._due.pop(0)  # missed while suspended: skip, never deliver late
                continue
            if focus:
                self._due[0] = now + DEFER_S  # postpone, do not disturb focus
                break
            self._fire()
            self._due.pop(0)
        self._arm()

    def _fire(self) -> None:
        self._notifier.notify(TITLE, self._body())

    def _body(self) -> str:
        title = None
        if self._top_title is not None:
            try:
                title = self._top_title()
            except Exception:  # noqa: BLE001 - a DB hiccup must not lose the reminder
                title = None
        if title:
            return f"Focus Check: Have you worked on '{title}' today?"
        return self._rng.choice(QUOTES)
