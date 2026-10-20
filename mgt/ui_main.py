"""Main window: goals, presets, Pomodoro bar, lazy Settings (IMPLEMENTATION.md §10).

Lifecycle: created **on demand** (``app.open_main_window``) and destroyed on
close via ``WA_DeleteOnClose`` so its widgets are freed while the app lives in
the tray. Everything that must survive a close (``selected_horizon``,
``show_completed``) lives in settings.

Two efficiency rules shape the code below:

* The task list is rebuilt **only** when the ordered ``(id, completed, title,
  deadline)`` signature changes — never on a timer.
* Time-driven badge updates touch only the *dynamic* rows (future deadline
  inside 72 h) in place, one wake per minute, and only while the window is
  visible. No list reload, so scroll position and focus are never lost.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import QDateTime, Qt, QTimer
from PyQt6.QtGui import QCursor, QGuiApplication
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from . import database, osenv, styles, timeutil
from .pomodoro import BREAK, PomodoroController, format_seconds
from .store import Store

DEFAULT_WIDTH = 460
DEFAULT_HEIGHT = 680
_SCREEN_FRACTION = 0.9
_PAD = 12
_MAX_TITLE = 200
_DYNAMIC_WINDOW_S = 72 * 3600
_TRIM_DELAY_MS = 3000

HORIZON_LABELS = {
    "today": "Today",
    "tomorrow": "Tomorrow",
    "week": "Week",
    "month": "Month",
    "year": "Year",
    "life": "Life",
}

PRESETS: Tuple[Tuple[str, str], ...] = (
    ("none", "No Deadline"),
    ("eod_today", "End of Today (23:59)"),
    ("plus_1h", "In 1 Hour (+1h)"),
    ("plus_2h", "In 2 Hours (+2h)"),
    ("plus_4h", "In 4 Hours (+4h)"),
    ("eod_tomorrow", "End of Tomorrow"),
    ("eow", "End of This Week (Sun 23:59)"),
    ("eom", "End of This Month"),
)
_CUSTOM_KEY = "custom"
_CUSTOM_LABEL = "Custom Date/Time..."


def _repolish(widget: QWidget) -> None:
    """Re-apply the app stylesheet to one widget after a property change."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


def _is_wayland() -> bool:
    app = QGuiApplication.instance()
    return app is not None and app.platformName() == "wayland"


class _TitleBar(QWidget):
    """Custom frameless titlebar: drag via ``startSystemMove``, ``✕`` closes."""

    def __init__(self, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._drag_origin = None

        row = QHBoxLayout(self)
        row.setContentsMargins(_PAD, _PAD, _PAD, 4)
        row.setSpacing(6)

        label = QLabel(title, self)
        label.setObjectName("Title")
        row.addWidget(label)
        row.addStretch(1)

        close_button = QPushButton("✕", self)
        close_button.setFixedWidth(28)
        close_button.setToolTip("Close (keeps running in the tray)")
        close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        close_button.clicked.connect(lambda: self.window().close())
        row.addWidget(close_button)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if (
                handle is not None
                and hasattr(handle, "startSystemMove")
                and handle.startSystemMove()
            ):
                event.accept()
                return
            self._drag_origin = (
                event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
            )
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag_origin)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_origin = None
        super().mouseReleaseEvent(event)


class _GoalRow(QWidget):
    """One task: checkbox, title, urgency badge, delete button."""

    def __init__(
        self,
        goal: Dict[str, Any],
        on_toggle,
        on_delete,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.goal_id = int(goal["id"])
        self.horizon = str(goal["horizon"])
        self.deadline: Optional[int] = goal["deadline"]

        self._full_title = str(goal["title"])

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(8)

        # setChecked *before* connecting, so restoring state never mutates the DB
        self.checkbox = QCheckBox(self)
        self.checkbox.setChecked(bool(goal["is_completed"]))
        self.checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self.checkbox.clicked.connect(lambda _checked: on_toggle(self.goal_id))
        row.addWidget(self.checkbox)

        self.title = QLabel(self._full_title, self)
        self.title.setObjectName("Title")
        self.title.setProperty("done", "true" if goal["is_completed"] else "false")
        self.title.setToolTip(self._full_title)
        self.title.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        row.addWidget(self.title, 1)

        self.badge = QLabel("", self)
        self.badge.setObjectName("Badge")
        row.addWidget(self.badge)

        delete_button = QPushButton("✕", self)
        delete_button.setFixedWidth(24)
        delete_button.setToolTip("Delete")
        delete_button.setCursor(Qt.CursorShape.PointingHandCursor)
        delete_button.clicked.connect(lambda: on_delete(self.goal_id))
        row.addWidget(delete_button)

        self.apply_status(int(time.time()))

    # --- content -----------------------------------------------------------

    def is_dynamic(self, now: int) -> bool:
        """Future deadline inside the 72 h window: worth a per-minute refresh."""
        return (
            self.deadline is not None
            and now < self.deadline <= now + _DYNAMIC_WINDOW_S
        )

    def apply_status(self, now: int) -> None:
        """Update text + ``urgency`` only when they actually changed (§14.3)."""
        text, _color, level = timeutil.calculate_deadline_status(
            self.deadline, self.horizon, now
        )
        if text != self.badge.text():
            self.badge.setText(text)
        if self.badge.property("urgency") != level:
            self.badge.setProperty("urgency", level)
            _repolish(self.badge)
        visible = bool(text)
        if self.badge.isVisible() != visible:
            self.badge.setVisible(visible)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        metrics = self.title.fontMetrics()
        width = max(40, self.title.width())
        elided = metrics.elidedText(
            self._full_title, Qt.TextElideMode.ElideRight, width
        )
        if elided != self.title.text():
            self.title.setText(elided)


class _CustomDeadlineDialog(QDialog):
    """Lazy date/time picker: calendar popup, default = now + 1 h (§10.4)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Custom Deadline")
        self.setModal(True)

        column = QVBoxLayout(self)
        column.setContentsMargins(_PAD, _PAD, _PAD, _PAD)
        column.setSpacing(8)

        self.edit = QDateTimeEdit(self)
        self.edit.setCalendarPopup(True)
        self.edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        column.addWidget(self.edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        column.addWidget(buttons)

        self.set_default_value()

    def set_default_value(self) -> None:
        self.edit.setDateTime(QDateTime.currentDateTime().addSecs(3600))

    def value_epoch(self) -> int:
        return int(self.edit.dateTime().toPyDateTime().timestamp())


class MainWindow(QWidget):
    """Goals + Settings window, created on demand and destroyed on close."""

    def __init__(
        self,
        store: Store,
        controller: PomodoroController,
        overlay: Optional[QWidget] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._controller = controller
        self._overlay = overlay

        horizon = store.setting("selected_horizon", "today") or "today"
        self._horizon = horizon if horizon in database.HORIZONS else "today"

        self._signature: Optional[Tuple[Any, ...]] = None
        self._rows: List[_GoalRow] = []
        self._dynamic_rows: List[_GoalRow] = []
        self._empty_label: Optional[QLabel] = None
        self._custom_deadline: Optional[int] = None
        self._custom_dialog: Optional[_CustomDeadlineDialog] = None
        self._settings_page: Optional[QWidget] = None
        self._badge_timer: Optional[QTimer] = None
        self._startup_module = None
        self._startup_initial = False

        self.setObjectName("MainWindow")
        self.setWindowTitle("Minimalist Goal Tracker")
        self.setWindowIcon(styles.app_icon())
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)

        self._build_ui()
        self._apply_geometry()
        self._update_deadline_dropdown()

        self._store.goals_changed.connect(self._on_goals_changed)
        self._controller.tick.connect(self._on_tick)
        self._controller.state_changed.connect(self._on_state_changed)
        self._sync_pomodoro()

    # --- construction ------------------------------------------------------

    def _build_ui(self) -> None:
        root = QFrame(self)
        root.setObjectName("Root")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(root)

        column = QVBoxLayout(root)
        column.setContentsMargins(0, 0, 0, _PAD)
        column.setSpacing(6)

        column.addWidget(_TitleBar("Minimalist Goal Tracker", root))
        column.addWidget(self._build_nav(root))

        self._stack = QStackedWidget(root)
        self._stack.addWidget(self._build_goals_page(root))
        column.addWidget(self._stack, 1)

        self._switch_tab(0)

    def _build_nav(self, parent: QWidget) -> QWidget:
        bar = QWidget(parent)
        row = QHBoxLayout(bar)
        row.setContentsMargins(_PAD, 0, _PAD, 0)
        row.setSpacing(4)

        self._nav_buttons: List[QPushButton] = []
        for index, text in ((0, "Goals"), (1, "Settings")):
            button = QPushButton(text, bar)
            button.setProperty("nav", "true")
            button.setProperty("active", "false")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked, i=index: self._switch_tab(i))
            row.addWidget(button)
            self._nav_buttons.append(button)
        row.addStretch(1)
        return bar

    def _build_goals_page(self, parent: QWidget) -> QWidget:
        page = QWidget(parent)
        column = QVBoxLayout(page)
        column.setContentsMargins(_PAD, 0, _PAD, 0)
        column.setSpacing(8)

        column.addWidget(self._build_horizon_bar(page))
        column.addWidget(self._build_quick_input(page))

        completed = QCheckBox("Show Completed", page)
        completed.setChecked(self._store.setting("show_completed") == "1")
        completed.toggled.connect(self._on_show_completed)
        column.addWidget(completed)
        self._completed_cb = completed

        self._scroll = QScrollArea(page)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        container = QWidget(self._scroll)
        self._list_layout = QVBoxLayout(container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(2)
        self._list_layout.addStretch(1)
        self._scroll.setWidget(container)
        self._list_container = container
        column.addWidget(self._scroll, 1)

        column.addWidget(self._build_pomodoro_bar(page))
        return page

    def _build_horizon_bar(self, parent: QWidget) -> QWidget:
        bar = QWidget(parent)
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)

        self._horizon_buttons: Dict[str, QPushButton] = {}
        for key in database.HORIZONS:
            button = QPushButton(HORIZON_LABELS[key], bar)
            button.setProperty("nav", "true")
            button.setProperty("active", "false")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked, k=key: self._select_horizon(k))
            row.addWidget(button)
            self._horizon_buttons[key] = button
        row.addStretch(1)
        self._sync_horizon_buttons()
        return bar

    def _build_quick_input(self, parent: QWidget) -> QWidget:
        card = QFrame(parent)
        card.setObjectName("Card")
        row = QHBoxLayout(card)
        row.setContentsMargins(6, 6, 6, 6)
        row.setSpacing(6)

        line = QLineEdit(card)
        line.setMaxLength(_MAX_TITLE)
        line.setPlaceholderText("Add a goal…")
        line.returnPressed.connect(self._add_goal)
        row.addWidget(line, 1)
        self._input = line

        combo = QComboBox(card)
        for key, label in PRESETS:
            combo.addItem(label, key)
        combo.addItem(_CUSTOM_LABEL, _CUSTOM_KEY)
        combo.currentIndexChanged.connect(self._on_preset_changed)
        row.addWidget(combo)
        self._preset_combo = combo

        add_button = QPushButton("Add", card)
        add_button.setCursor(Qt.CursorShape.PointingHandCursor)
        add_button.clicked.connect(self._add_goal)
        row.addWidget(add_button)
        return card

    def _build_pomodoro_bar(self, parent: QWidget) -> QWidget:
        card = QFrame(parent)
        card.setObjectName("Card")
        row = QHBoxLayout(card)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(8)

        self._time_label = QLabel(format_seconds(self._controller.remaining_seconds), card)
        self._time_label.setObjectName("Title")
        row.addWidget(self._time_label)

        self._mode_label = QLabel("Focus", card)
        self._mode_label.setObjectName("Badge")
        row.addWidget(self._mode_label)
        row.addStretch(1)

        self._toggle_button = QPushButton("Play", card)
        self._toggle_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_button.clicked.connect(self._controller.toggle)
        row.addWidget(self._toggle_button)

        reset_button = QPushButton("Reset", card)
        reset_button.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_button.clicked.connect(self._controller.reset)
        row.addWidget(reset_button)
        return card

    # --- geometry (§10.2) --------------------------------------------------

    def _apply_geometry(self) -> None:
        screen = None
        if QGuiApplication.instance() is not None:
            screen = QGuiApplication.screenAt(QCursor.pos())
            if screen is None:
                screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.resize(DEFAULT_WIDTH, DEFAULT_HEIGHT)
            return

        avail = screen.availableGeometry()
        width = max(320, min(DEFAULT_WIDTH, int(avail.width() * _SCREEN_FRACTION)))
        height = max(360, min(DEFAULT_HEIGHT, int(avail.height() * _SCREEN_FRACTION)))
        self.resize(width, height)

        if _is_wayland():
            return  # compositor decides placement
        self.move(
            avail.x() + (avail.width() - self.width()) // 2,
            avail.y() + (avail.height() - self.height()) // 2,
        )

    # --- tabs (§10.3) ------------------------------------------------------

    def _switch_tab(self, index: int) -> None:
        if index == 1 and self._settings_page is None:
            self._settings_page = self._build_settings_page(self)
            self._stack.addWidget(self._settings_page)
        self._stack.setCurrentIndex(index)
        for position, button in enumerate(self._nav_buttons):
            self._set_active(button, position == index)

    @staticmethod
    def _set_active(button: QPushButton, active: bool) -> None:
        value = "true" if active else "false"
        if button.property("active") != value:
            button.setProperty("active", value)
            _repolish(button)

    def _sync_horizon_buttons(self) -> None:
        for key, button in self._horizon_buttons.items():
            self._set_active(button, key == self._horizon)

    # --- goals (§10.4) -----------------------------------------------------

    def _select_horizon(self, horizon: str) -> None:
        if horizon == self._horizon:
            return
        self._horizon = horizon
        self._store.set_setting("selected_horizon", horizon)
        self._sync_horizon_buttons()
        self._update_deadline_dropdown()
        self._rebuild(force=True)

    def _update_deadline_dropdown(self) -> None:
        allow_choice, default_ts, options = timeutil.get_horizon_deadline_config(self._horizon)

        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        for label, key in options:
            self._preset_combo.addItem(label, key)
        self._preset_combo.blockSignals(False)

        if allow_choice:
            self._preset_combo.setEnabled(True)
            self._preset_combo.setVisible(True)
            if hasattr(self, '_lbl_auto_deadline'):
                self._lbl_auto_deadline.setVisible(False)
        else:
            self._preset_combo.setVisible(False)
            if not hasattr(self, '_lbl_auto_deadline'):
                self._lbl_auto_deadline = QLabel(options[0][0] if options else "", self._preset_combo.parent())
                self._lbl_auto_deadline.setObjectName("Badge")
            else:
                self._lbl_auto_deadline.setText(options[0][0] if options else "")
            self._lbl_auto_deadline.setVisible(True)

    def _on_show_completed(self, checked: bool) -> None:
        self._store.set_setting("show_completed", "1" if checked else "0")
        self._rebuild(force=True)

    def _on_preset_changed(self, index: int) -> None:
        if self._preset_combo.itemData(index) == _CUSTOM_KEY:
            self._prompt_custom_deadline()

    def _prompt_custom_deadline(self) -> None:
        if self._custom_dialog is None:
            self._custom_dialog = _CustomDeadlineDialog(self)
        self._custom_dialog.set_default_value()
        if self._custom_dialog.exec() == QDialog.DialogCode.Accepted:
            self._custom_deadline = self._custom_dialog.value_epoch()
            return
        # Cancelled: fall back to "No Deadline" so Add cannot use a stale value.
        self._custom_deadline = None
        self._reset_preset_combo()

    def _reset_preset_combo(self) -> None:
        combo = self._preset_combo
        combo.blockSignals(True)
        combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def _current_deadline(self) -> Optional[int]:
        allow_choice, default_ts, _ = timeutil.get_horizon_deadline_config(self._horizon)
        if not allow_choice:
            return default_ts
        key = self._preset_combo.currentData()
        if key == _CUSTOM_KEY:
            return self._custom_deadline
        if key == "none" or not key:
            return None
        return timeutil.compute_preset(str(key), datetime.now())

    def _add_goal(self) -> None:
        title = self._input.text().strip()
        if not title:
            return
        self._store.add_goal(title, self._horizon, self._current_deadline())
        self._input.clear()
        self._custom_deadline = None
        self._reset_preset_combo()

    def _on_goals_changed(self) -> None:
        self._rebuild()

    def _rebuild(self, force: bool = False) -> None:
        """Rebuild the rows only when the rendered signature changed (§10.4)."""
        goals = self._store.goals(self._horizon, self._completed_cb.isChecked())
        signature = (
            self._horizon,
            tuple(
                (g["id"], g["is_completed"], g["title"], g["deadline"]) for g in goals
            ),
        )
        if not force and signature == self._signature:
            return
        self._signature = signature

        for row in self._rows:
            self._list_layout.removeWidget(row)
            row.setParent(None)
            row.deleteLater()
        self._rows = []

        if self._empty_label is not None:
            self._list_layout.removeWidget(self._empty_label)
            self._empty_label.deleteLater()
            self._empty_label = None

        if not goals:
            self._empty_label = QLabel("No goals here yet", self._list_container)
            self._empty_label.setObjectName("Badge")
            self._list_layout.insertWidget(0, self._empty_label)

        now = int(time.time())
        dynamic: List[_GoalRow] = []
        for index, goal in enumerate(goals):
            row = _GoalRow(goal, self._toggle_goal, self._delete_goal, self._list_container)
            self._list_layout.insertWidget(index, row)
            self._rows.append(row)
            if row.is_dynamic(now):
                dynamic.append(row)
        self._dynamic_rows = dynamic

        if self.isVisible():
            self._refresh_badges()

    def _toggle_goal(self, goal_id: int) -> None:
        self._store.toggle_goal(goal_id)

    def _delete_goal(self, goal_id: int) -> None:
        self._store.delete_goal(goal_id)

    # --- in-place badge refresh (§10.4) ------------------------------------

    def _badge_timer_instance(self) -> QTimer:
        if self._badge_timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setTimerType(Qt.TimerType.CoarseTimer)
            timer.timeout.connect(self._on_badge_timeout)
            self._badge_timer = timer
        return self._badge_timer

    def _arm_badge_timer(self) -> None:
        """One wake aligned to the next minute boundary (+1 s) — no polling."""
        now = time.time()
        next_boundary = (int(now // 60) + 1) * 60 + 1
        self._badge_timer_instance().start(max(1000, int((next_boundary - now) * 1000)))

    def _on_badge_timeout(self) -> None:
        self._refresh_badges()

    def _refresh_badges(self) -> None:
        now = int(time.time())
        still_dynamic: List[_GoalRow] = []
        for row in self._dynamic_rows:
            row.apply_status(now)
            if row.is_dynamic(now):
                still_dynamic.append(row)
        self._dynamic_rows = still_dynamic
        if self.isVisible():
            self._arm_badge_timer()

    # --- Pomodoro bar ------------------------------------------------------

    def _sync_pomodoro(self) -> None:
        text = format_seconds(self._controller.remaining_seconds)
        if text != self._time_label.text():
            self._time_label.setText(text)
        mode = "Break" if self._controller.current_mode == BREAK else "Focus"
        if mode != self._mode_label.text():
            self._mode_label.setText(mode)
        label = "Pause" if self._controller.is_running else "Play"
        if label != self._toggle_button.text():
            self._toggle_button.setText(label)

    def _on_tick(self, text: str, _seconds: int) -> None:
        if text != self._time_label.text():
            self._time_label.setText(text)

    def _on_state_changed(self, _running: bool, _mode: str) -> None:
        self._sync_pomodoro()

    # --- Settings (§10.5, built lazily) ------------------------------------

    def _build_settings_page(self, parent: QWidget) -> QWidget:
        page = QWidget(parent)
        column = QVBoxLayout(page)
        column.setContentsMargins(_PAD, 0, _PAD, _PAD)
        column.setSpacing(8)

        card = QFrame(page)
        card.setObjectName("Card")
        form = QVBoxLayout(card)
        form.setContentsMargins(_PAD, _PAD, _PAD, _PAD)
        form.setSpacing(8)

        self._focus_spin = QSpinBox(card)
        self._focus_spin.setRange(1, 120)
        self._focus_spin.setValue(self._controller.focus_duration_minutes)
        form.addWidget(self._labelled("Focus minutes", self._focus_spin, card))

        self._break_spin = QSpinBox(card)
        self._break_spin.setRange(1, 60)
        self._break_spin.setValue(self._controller.break_duration_minutes)
        form.addWidget(self._labelled("Break minutes", self._break_spin, card))

        self._auto_start_cb = QCheckBox("Auto-start next phase (Focus ⇄ Break)", card)
        self._auto_start_cb.setChecked(
            self._store.setting("pomodoro_auto_start") == "1"
        )
        form.addWidget(self._auto_start_cb)

        self._overlay_cb = QCheckBox("Show overlay", card)
        self._overlay_cb.setChecked(self._store.setting("overlay_enabled") == "1")
        form.addWidget(self._overlay_cb)

        self._on_top_cb = QCheckBox("Overlay always on top", card)
        self._on_top_cb.setChecked(self._store.setting("overlay_on_top") == "1")
        if _is_wayland():
            self._on_top_cb.setEnabled(False)
            form.addWidget(self._hint("Not supported by this display server", card))
        form.addWidget(self._on_top_cb)

        self._reminders_cb = QCheckBox("Random reminders", card)
        self._reminders_cb.setChecked(self._store.setting("reminders_enabled") == "1")
        form.addWidget(self._reminders_cb)

        form.addWidget(self._build_startup_row(card))
        column.addWidget(card)

        save = QPushButton("Save Settings", page)
        save.setCursor(Qt.CursorShape.PointingHandCursor)
        save.clicked.connect(self._save_settings)
        column.addWidget(save)

        self._status_label = QLabel("", page)
        self._status_label.setObjectName("Badge")
        column.addWidget(self._status_label)
        column.addStretch(1)
        return page

    def _build_startup_row(self, card: QWidget) -> QWidget:
        """In-process autostart toggle (startup.py, Phase 7) with WSL fallback."""
        holder = QWidget(card)
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)

        checkbox = QCheckBox("Start on login", holder)
        self._startup_cb = checkbox
        column.addWidget(checkbox)

        reason = ""
        if osenv.IS_WSL:
            reason = "Not supported under WSL"
        else:
            try:
                from . import startup as startup_module

                self._startup_module = startup_module
            except Exception:  # phase 7 not landed yet
                self._startup_module = None
            if self._startup_module is None:
                reason = "Not available yet"

        if reason:
            checkbox.setEnabled(False)
            column.addWidget(self._hint(reason, holder))
        else:
            getter = getattr(self._startup_module, "is_enabled", None)
            if callable(getter):
                try:
                    self._startup_initial = bool(getter())
                except Exception:
                    self._startup_initial = False
            checkbox.setChecked(self._startup_initial)
        return holder

    @staticmethod
    def _labelled(text: str, field: QWidget, parent: QWidget) -> QWidget:
        holder = QWidget(parent)
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        label = QLabel(text, holder)
        label.setObjectName("Title")
        row.addWidget(label)
        row.addStretch(1)
        field.setParent(holder)
        row.addWidget(field)
        return holder

    @staticmethod
    def _hint(text: str, parent: QWidget) -> QLabel:
        label = QLabel(text, parent)
        label.setObjectName("Badge")
        return label

    def _save_settings(self) -> None:
        focus = self._focus_spin.value()
        breaks = self._break_spin.value()
        self._store.set_setting("pomodoro_focus_min", str(focus))
        self._store.set_setting("pomodoro_break_min", str(breaks))
        self._controller.update_durations(focus, breaks)

        auto_start = self._auto_start_cb.isChecked()
        self._store.set_setting("pomodoro_auto_start", "1" if auto_start else "0")
        self._controller.auto_start = auto_start

        self._store.set_setting("overlay_enabled", "1" if self._overlay_cb.isChecked() else "0")
        self._store.set_setting("overlay_on_top", "1" if self._on_top_cb.isChecked() else "0")
        self._store.set_setting(
            "reminders_enabled", "1" if self._reminders_cb.isChecked() else "0"
        )

        if self._overlay is not None:
            self._overlay.apply_flags()
            self._overlay.setVisible(self._overlay_cb.isChecked())

        note = "Saved"
        if self._startup_module is not None:
            wanted = self._startup_cb.isChecked()
            if wanted != self._startup_initial:
                try:
                    if wanted:
                        self._startup_module.enable()
                    else:
                        self._startup_module.disable()
                    self._startup_initial = wanted
                except Exception:
                    note = "Saved, but autostart could not be updated"
        self._status_label.setText(note)

    # --- events ------------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802
        self._rebuild(force=True)  # one immediate refresh on becoming visible
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        if self._badge_timer is not None:
            self._badge_timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._badge_timer is not None:
            self._badge_timer.stop()
        QTimer.singleShot(_TRIM_DELAY_MS, osenv.trim_memory)
        if not self._has_alternative_surface():
            QApplication.quit()
        super().closeEvent(event)

    def _has_alternative_surface(self) -> bool:
        """Closing to nothing would make the app unreachable (§10.1)."""
        if self._overlay is not None and self._overlay.isVisible():
            return True
        try:
            return bool(QSystemTrayIcon.isSystemTrayAvailable())
        except Exception:
            return False
