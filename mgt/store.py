"""State hub: the only object UI code talks to.

Wraps ``database`` mutations behind one coalesced ``goals_changed`` signal, so a
burst of edits triggers exactly one UI rebuild. Settings are cached in memory at
startup: reads never touch SQLite, writes are write-through. Also owns the
midnight rollover timer.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal

from . import database, timeutil

# Never sleep longer than this between rollover checks, so a suspend/resume
# still rolls the date over promptly (§11, §14).
_MAX_WAKE_MS = 15 * 60 * 1000


class Store(QObject):
    """Cached settings + DB mutations + a coalesced ``goals_changed`` signal."""

    goals_changed = pyqtSignal()

    def __init__(
        self,
        parent: Optional[QObject] = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        super().__init__(parent)
        self._now = now
        self._settings: Dict[str, str] = database.get_all_settings()
        self._dirty = False
        self._flush_pending = False
        self._midnight_timer: Optional[QTimer] = None
        self._arm_midnight()

    # --- settings ---------------------------------------------------------

    def setting(self, key: str, default: str = "") -> str:
        """Cached read; never touches SQLite."""
        return self._settings.get(key, default)

    def set_setting(self, key: str, value: str) -> None:
        """Write-through: update SQLite only when the value actually changed."""
        value = str(value)
        if self._settings.get(key) == value:
            return
        database.set_setting(key, value)
        self._settings[key] = value

    # --- goals ------------------------------------------------------------

    def add_goal(self, title: str, horizon: str, deadline: Optional[int] = None) -> int:
        if deadline is None and horizon in ("today", "tomorrow", "week", "month", "year"):
            _, deadline, _ = timeutil.get_horizon_deadline_config(horizon)
        goal_id = database.add_goal(title, horizon, deadline)
        self._touch()
        return goal_id

    def toggle_goal(self, goal_id: int) -> bool:
        state = database.toggle_goal_completion(goal_id)
        self._touch()
        return state

    def delete_goal(self, goal_id: int) -> bool:
        deleted = database.delete_goal(goal_id)
        if deleted:
            self._touch()
        return deleted

    def goals(
        self, horizon: Optional[str] = None, show_completed: bool = False
    ) -> List[Dict[str, Any]]:
        return database.get_goals(horizon, show_completed)

    def top_goal_titles(self, n: int) -> List[str]:
        return database.top_goal_titles(n)

    def top_goals(self, n: int) -> List[Dict[str, Any]]:
        return database.top_goals(n)

    # --- coalesced change notification ------------------------------------

    def _touch(self) -> None:
        """Mark goals dirty and schedule one emission for the end of the burst."""
        self._dirty = True
        if not self._flush_pending:
            self._flush_pending = True
            QTimer.singleShot(0, self._flush)

    def _flush(self) -> None:
        self._flush_pending = False
        if self._dirty:
            self._dirty = False
            self.goals_changed.emit()

    def flush(self) -> None:
        """Emit any pending ``goals_changed`` immediately (also for tests)."""
        self._flush()

    # --- local-midnight rollover ------------------------------------------

    def _timer_instance(self) -> QTimer:
        if self._midnight_timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setTimerType(Qt.TimerType.CoarseTimer)
            timer.timeout.connect(self._on_midnight)
            self._midnight_timer = timer
        return self._midnight_timer

    def _arm_midnight(self) -> None:
        now = self._now()
        tomorrow = datetime.fromtimestamp(now) + timedelta(days=1)
        wake_at = tomorrow.replace(hour=0, minute=0, second=5, microsecond=0)
        ms = int((wake_at.timestamp() - now) * 1000)
        self._timer_instance().start(max(1000, min(ms, _MAX_WAKE_MS)))

    def _on_midnight(self) -> None:
        before = database.get_setting("last_rollover_date", "")
        database.rollover_horizons(date.today())
        if database.get_setting("last_rollover_date", "") != before:
            self._touch()  # only when the date actually moved
        self._arm_midnight()
