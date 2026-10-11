"""Read-only floating overlay (IMPLEMENTATION.md §9).

A frameless, never-focusing, always-on-top strip that shows today's top
priorities plus a mini Pomodoro bar. It is strictly read-only: rows are plain
labels, there are no checkboxes and no per-row click handlers, so dragging the
window can never mutate a task.

Geometry is deterministic. Word-wrapped labels whose height depends on their
width feed ``layout().sizeHint()`` back into the window size, which grows
without bound across DPI changes (v1's bug). Here the width is a constant, a
fixed pool of single-line elided labels is created once and only re-texted, and
the height is pure arithmetic — never derived from the layout.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import List, Optional, Tuple

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .pomodoro import BREAK, PomodoroController, format_seconds
from .store import Store
from .timeutil import calculate_deadline_status

#: Logical-px constants. The height is computed from these, never from a layout
#: size hint (§9.3), so a font-metric change can never make the window drift.
WIDTH = 300
BADGE_WIDTH = 64   # Fixed logical px allocated for deadline badges
MAX_ROWS = 6
PAD = 10
MARGIN = 20          # gap from the screen edge when auto-placing
_MIN_VISIBLE = 40    # a restored rect must show at least this much on a screen
_DIVIDER_H = 1       # 1px horizontal divider between header and task list

_BANNER_MS = 20_000
_POSITION_DEBOUNCE_MS = 500


class OverlayRowWidgets:
    """Horizontal container for one task row: title + urgency badge side by side."""

    def __init__(self, row_widget: QWidget, title_lbl: QLabel, badge_lbl: QLabel):
        self.row_widget = row_widget
        self.title_lbl = title_lbl
        self.badge_lbl = badge_lbl

    def set_visible(self, visible: bool):
        self.row_widget.setVisible(visible)


class OverlayWidget(QWidget):
    """Always-on-top read-only summary of today's goals + mini Pomodoro."""

    #: Emitted on double-click / the context menu's "Open Main Window".
    open_requested = pyqtSignal()
    #: Emitted by the context menu's "Quit" (the tray may not exist, §9.2).
    quit_requested = pyqtSignal()

    def __init__(
        self,
        store: Store,
        controller: PomodoroController,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._controller = controller

        self._resyncing = False
        self._screen_hooked = False
        self._position_restored = False
        self._drag_origin: Optional[QPoint] = None
        self._signature: Optional[Tuple[Tuple[str, ...], int]] = None
        self._shown_rows = 0
        self._hidden_count = 0
        self._banner_timer: Optional[QTimer] = None
        self._badge_timer: Optional[QTimer] = None
        self._save_timer: Optional[QTimer] = None

        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setObjectName("Overlay")

        metrics = self.fontMetrics()
        self._row_h = metrics.height() + 6
        self._header_h = max(metrics.height() + 8, 30)
        self._elide_width = WIDTH - 2 * PAD

        self._build_ui()
        self._apply_flags()

        self._apply_content(force=True)  # paint once now; goals_changed re-runs it

        store.goals_changed.connect(self._on_goals_changed)
        controller.tick.connect(self._on_tick)
        controller.state_changed.connect(self._on_state_changed)

        app = QGuiApplication.instance()
        if app is not None:  # dock/undock can strand the overlay off-screen
            app.screenAdded.connect(self._on_screens_changed)
            app.screenRemoved.connect(self._on_screens_changed)
            app.primaryScreenChanged.connect(self._on_screens_changed)

    # --- construction ------------------------------------------------------

    def _build_ui(self) -> None:
        root = QFrame(self)
        root.setObjectName("Root")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(root)

        column = QVBoxLayout(root)
        column.setContentsMargins(PAD, PAD, PAD, PAD)
        column.setSpacing(0)

        column.addWidget(self._build_header())

        self._divider = QFrame(root)
        self._divider.setObjectName("Divider")
        self._divider.setFixedHeight(_DIVIDER_H)
        self._divider.setLineWidth(0)
        self._divider.setFrameShape(QFrame.Shape.HLine)
        self._divider.setStyleSheet(
            "background-color: #222222; margin-top: 4px; margin-bottom: 6px;"
        )
        column.addWidget(self._divider)

        self._banner_label = QLabel("", root)
        self._banner_label.setObjectName("Title")
        self._banner_label.setFixedHeight(self._row_h)
        self._banner_label.setVisible(False)
        column.addWidget(self._banner_label)

        # A fixed pool: each row is a horizontal container with title + badge (§9.3).
        self._row_pairs: List[OverlayRowWidgets] = []
        for _ in range(MAX_ROWS):
            row_widget = QWidget(root)
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)
            row_widget.setFixedHeight(self._row_h)
            row_widget.setVisible(False)

            title_lbl = QLabel("", row_widget)
            title_lbl.setObjectName("Title")
            title_lbl.setFixedHeight(self._row_h)
            row_layout.addWidget(title_lbl, stretch=1)

            badge_lbl = QLabel("", row_widget)
            badge_lbl.setObjectName("Badge")
            badge_lbl.setFixedHeight(self._row_h)
            row_layout.addWidget(badge_lbl)

            column.addWidget(row_widget)
            self._row_pairs.append(OverlayRowWidgets(row_widget, title_lbl, badge_lbl))

        # "+N more" label wrapped in a horizontal container for consistent layout
        more_row = QWidget(root)
        more_layout = QHBoxLayout(more_row)
        more_layout.setContentsMargins(0, 0, 0, 0)
        more_layout.setSpacing(6)
        more_row.setFixedHeight(self._row_h)
        more_row.setVisible(False)

        self._more_label = QLabel("", more_row)
        self._more_label.setObjectName("Badge")
        self._more_label.setFixedHeight(self._row_h)
        more_layout.addWidget(self._more_label, stretch=1)
        more_layout.addStretch(1)
        column.addWidget(more_row)
        self._more_row = more_row

    def _build_header(self) -> QWidget:
        header = QWidget(self)
        header.setFixedHeight(self._header_h)
        row = QHBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        title = QLabel(datetime.now().strftime("%a, %b %d"), header)
        title.setObjectName("Title")
        row.addWidget(title)
        row.addStretch(1)

        self._time_label = QLabel(format_seconds(self._controller.remaining_seconds), header)
        self._time_label.setObjectName("Title")
        row.addWidget(self._time_label)

        self._mode_label = QLabel("Focus", header)
        self._mode_label.setObjectName("Badge")
        row.addWidget(self._mode_label)

        self._toggle_button = QPushButton("Play", header)
        self._toggle_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_button.clicked.connect(self._controller.toggle)
        row.addWidget(self._toggle_button)

        self._sync_mini_bar()
        return header

    # --- window flags ------------------------------------------------------

    def _apply_flags(self) -> None:
        """(Re)build the window flags from ``overlay_on_top``.

        Changing flags on a live window hides it, so a visible overlay is
        re-shown afterwards (§9.1).
        """
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        if self._store.setting("overlay_on_top") == "1":
            flags |= Qt.WindowType.WindowStaysOnTopHint

        if flags == self.windowFlags():
            return
        was_visible = self.isVisible()
        self.setWindowFlags(flags)
        if was_visible:
            self.show()

    def apply_flags(self) -> None:
        """Public hook for the Settings page (§10)."""
        self._apply_flags()

    # --- content -----------------------------------------------------------

    def _on_goals_changed(self) -> None:
        self._apply_content()

    def refresh(self) -> None:
        """Force a re-render (e.g. after a theme/font change)."""
        self._apply_content(force=True)

    def _apply_content(self, force: bool = False) -> None:
        """Re-text the label pool. Idempotent via a content signature."""
        if self._resyncing:
            return
        self._resyncing = True
        try:
            goals = self._store.top_goals(MAX_ROWS)
            titles = [g["title"] for g in goals]
            hidden = max(0, self._today_count() - MAX_ROWS)
            signature = (tuple(titles), hidden)
            if not force and signature == self._signature:
                return
            self._signature = signature

            if titles:
                fm = self._row_pairs[0].title_lbl.fontMetrics()
                avail_title_w = WIDTH - 2 * PAD - BADGE_WIDTH - 6
                now = int(time.time())
                for index, pair in enumerate(self._row_pairs):
                    if index < len(goals):
                        pair.set_visible(True)
                        goal = goals[index]
                        full = goal["title"]
                        pair.title_lbl.setText(
                            fm.elidedText(
                                "• " + full, Qt.TextElideMode.ElideRight, avail_title_w
                            )
                        )
                        pair.title_lbl.setToolTip(full)

                        st = calculate_deadline_status(goal["deadline"], "today", now)
                        pair.badge_lbl.setText(st.label)
                        if pair.badge_lbl.property("urgency") != st.level:
                            pair.badge_lbl.setProperty("urgency", st.level)
                            pair.badge_lbl.style().unpolish(pair.badge_lbl)
                            pair.badge_lbl.style().polish(pair.badge_lbl)
                        pair.badge_lbl.setVisible(bool(st.label))
                    else:
                        pair.set_visible(False)
                self._shown_rows = len(titles)
            else:
                self._row_pairs[0].set_visible(True)
                self._row_pairs[0].title_lbl.setText("Nothing planned for today")
                self._row_pairs[0].title_lbl.setToolTip("")
                self._row_pairs[0].badge_lbl.setVisible(False)
                for pair in self._row_pairs[1:]:
                    pair.set_visible(False)
                self._shown_rows = 1

            self._hidden_count = hidden
            self._more_label.setText(f"+{hidden} more")
            self._more_row.setVisible(hidden > 0)
            self._resize_to_content()
        finally:
            self._resyncing = False

    def _today_count(self) -> int:
        """Total uncompleted goals today, for the ``+N more`` row."""
        return len(self._store.goals("today"))

    def _resize_to_content(self) -> None:
        """Arithmetic height — never ``layout().sizeHint()`` (§9.3)."""
        height = (
            2 * PAD
            + self._header_h
            + _DIVIDER_H
            + max(self._shown_rows, 1) * self._row_h
            + (self._row_h if self._hidden_count else 0)
            + (self._row_h if self._banner_label.isVisible() else 0)
        )
        if (WIDTH, height) != (self.width(), self.height()):
            self.setFixedSize(WIDTH, height)

    def _refresh_badges(self) -> None:
        """Refresh urgency badges in-place without rebuilding layout."""
        goals = self._store.top_goals(MAX_ROWS)
        now = int(time.time())
        for index, pair in enumerate(self._row_pairs):
            if index < len(goals):
                st = calculate_deadline_status(goals[index]["deadline"], "today", now)
                pair.badge_lbl.setText(st.label)
                if pair.badge_lbl.property("urgency") != st.level:
                    pair.badge_lbl.setProperty("urgency", st.level)
                    pair.badge_lbl.style().unpolish(pair.badge_lbl)
                    pair.badge_lbl.style().polish(pair.badge_lbl)

    # --- banner (§11 fallback notification) --------------------------------

    def show_banner(self, text: str) -> None:
        """Show ``text`` in the banner row for 20 s (works even under WSLg)."""
        fm = self._banner_label.fontMetrics()
        self._banner_label.setText(
            fm.elidedText(text, Qt.TextElideMode.ElideRight, self._elide_width)
        )
        self._banner_label.setToolTip(text)
        self._banner_label.setVisible(True)
        self._resize_to_content()
        self._banner_timer_instance().start(_BANNER_MS)

    def hide_banner(self) -> None:
        if not self._banner_label.isVisible():
            return
        self._banner_timer_instance().stop()
        self._banner_label.setVisible(False)
        self._resize_to_content()

    @property
    def banner_visible(self) -> bool:
        return self._banner_label.isVisible()

    def _arm_badge_timer(self) -> None:
        """Arm single-shot timer to fire at the next minute boundary."""
        if not self.isVisible():
            return
        now = time.time()
        next_boundary = (int(now // 60) + 1) * 60 + 1
        delay_ms = max(1000, int((next_boundary - now) * 1000))
        self._badge_timer_instance().start(delay_ms)

    def _badge_timer_instance(self) -> QTimer:
        """Create/lazy-init the badge timer."""
        if self._badge_timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setTimerType(Qt.TimerType.CoarseTimer)
            timer.timeout.connect(self._on_badge_timeout)
            self._badge_timer = timer
        return self._badge_timer

    def _on_badge_timeout(self) -> None:
        """Called when badge timer fires."""
        self._refresh_badges()
        # Update header date in case midnight passed
        self._build_header()
        if self.isVisible():
            self._arm_badge_timer()

    def _banner_timer_instance(self) -> QTimer:
        if self._banner_timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setTimerType(Qt.TimerType.CoarseTimer)
            timer.timeout.connect(self.hide_banner)
            self._banner_timer = timer
        return self._banner_timer

    # --- mini bar ----------------------------------------------------------

    def _sync_mini_bar(self) -> None:
        """Pull the whole bar from the controller (used on show / state change)."""
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
        if not self.isVisible():
            return
        if text != self._time_label.text():
            self._time_label.setText(text)
        # Refresh urgency badges in-place without rebuilding layout
        self._refresh_badges()

    def _on_state_changed(self, _running: bool, _mode: str) -> None:
        if not self.isVisible():
            return
        self._sync_mini_bar()

    # --- events ------------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if not self._screen_hooked:
            handle = self.windowHandle()
            if handle is not None:
                handle.screenChanged.connect(self._on_screen_changed)
                self._screen_hooked = True
        if not self._position_restored:
            self._position_restored = True
            self.restore_position()
        self._sync_mini_bar()
        self._apply_content(force=True)
        # Start badge timer when shown
        self._arm_badge_timer()
        super().showEvent(event)
        if not self._screen_hooked:
            handle = self.windowHandle()
            if handle is not None:  # None before the first show (§9.3)
                handle.screenChanged.connect(self._on_screen_changed)
                self._screen_hooked = True
        if not self._position_restored:
            self._position_restored = True
            self.restore_position()
        self._sync_mini_bar()
        self._apply_content(force=True)
        super().showEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.windowHandle()
            if handle is not None and hasattr(handle, "startSystemMove"):
                if handle.startSystemMove():
                    event.accept()
                    return
            # Manual fallback for compositors that refuse a system move.
            self._drag_origin = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_origin)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_origin = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.open_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = QMenu(self)
        open_action = menu.addAction("Open Main Window")
        timer_action = menu.addAction(
            "Pause Timer" if self._controller.is_running else "Start Timer"
        )
        menu.addSeparator()
        hide_action = menu.addAction("Hide Overlay")
        menu.addSeparator()
        quit_action = menu.addAction("Quit")

        chosen = menu.exec(event.globalPos())
        if chosen is open_action:
            self.open_requested.emit()
        elif chosen is timer_action:
            self._controller.toggle()
        elif chosen is hide_action:
            self._hide_self()
        elif chosen is quit_action:
            self.quit_requested.emit()

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        if self._is_wayland():
            return  # Wayland ignores client-side moves; nothing to persist
        self._save_timer_instance().start(_POSITION_DEBOUNCE_MS)

    # --- position ----------------------------------------------------------

    def restore_position(self) -> None:
        """Restore the saved spot, clamped onto a real screen (§9.4)."""
        if self._is_wayland():
            return
        x = self._store.setting("overlay_x", "")
        y = self._store.setting("overlay_y", "")
        try:
            point = QPoint(int(x), int(y))
        except ValueError:
            self._move_to_default()
            return
        self.move(point)
        self._clamp_to_screens()

    def _move_to_default(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        self.move(avail.x() + avail.width() - WIDTH - MARGIN, avail.y() + MARGIN)

    def _clamp_to_screens(self) -> None:
        if self._is_wayland():
            return
        rect = QRect(self.pos(), QSize(WIDTH, max(self.height(), 1)))
        for screen in QGuiApplication.screens():
            visible = screen.availableGeometry().intersected(rect)
            if visible.width() >= _MIN_VISIBLE and visible.height() >= _MIN_VISIBLE:
                return
        self._move_to_default()

    def _on_screens_changed(self, *_args) -> None:
        self._clamp_to_screens()

    def _on_screen_changed(self, _screen) -> None:
        # New screen => new device pixel ratio => re-elide with its metrics.
        self._apply_content(force=True)
        self._clamp_to_screens()

    def _save_timer_instance(self) -> QTimer:
        if self._save_timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setTimerType(Qt.TimerType.CoarseTimer)
            timer.timeout.connect(self._persist_position)
            self._save_timer = timer
        return self._save_timer

    def _persist_position(self) -> None:
        if self._is_wayland():
            return
        point = self.pos()
        self._store.set_setting("overlay_x", str(point.x()))
        self._store.set_setting("overlay_y", str(point.y()))

    @staticmethod
    def _is_wayland() -> bool:
        app = QGuiApplication.instance()
        return app is not None and app.platformName() == "wayland"

    def _hide_self(self) -> None:
        """Hide and remember it, so the tray's "Toggle Overlay" can restore it."""
        self.hide()
        self._store.set_setting("overlay_enabled", "0")

    def toggle_visibility(self) -> None:
        """Tray hook (§13.1): hide, or show remembering the preference."""
        if self.isVisible():
            self._hide_self()
        else:
            self.flush_position()
            self._store.set_setting("overlay_enabled", "1")
            self.apply_flags()
            self.show()

    def flush_position(self) -> None:
        """Persist the debounced position now (used on hide/quit)."""
        if self._save_timer is not None:
            self._save_timer.stop()
        self._persist_position()
        # Stop badge timer when hidden
        if self._badge_timer is not None:
            self._badge_timer.stop()
