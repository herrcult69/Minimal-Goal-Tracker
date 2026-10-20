"""Application lifecycle, tray wiring, argument handling, and --doctor (§13).

Startup order matters (``main``): parse argv, bootstrap Qt env + logging, build
the QApplication, apply OS tweaks, take the single-instance lock, open the DB,
wire Store/Pomodoro/overlay/tray/reminders, then enter the event loop.

The module stays Qt-free at import time: ``osenv.configure_qt_environment()``
must run *before* Qt is imported, so every Qt import lives inside a function.
"""

from __future__ import annotations

import gc
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

from . import log, osenv

VERSION = "0.2.0"

_logger = logging.getLogger("mgt.app")


def main() -> int:
    args = sys.argv[1:]
    minimized = "--minimized" in args
    doctor = "--doctor" in args

    if "--version" in args:
        print(f"MGT {VERSION}")
        return 0

    osenv.configure_qt_environment()
    log.setup()

    if doctor:
        return _doctor()

    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QGuiApplication
    from PyQt6.QtWidgets import QApplication

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    log.install_qt_message_handler()

    osenv.apply_efficiency_tweaks()  # AUMID must precede any window creation

    from . import database
    from .single_instance import acquire

    instance = acquire()
    if instance is None:
        return 0  # another launch owns the lock; it was signalled to show

    try:
        database.init_db()
        app = Application(qapp, minimized=minimized)
        instance.show_requested.connect(app.open_main_window)
        app.start()
    except Exception:  # noqa: BLE001 - a silent boot must still reach the log
        _logger.critical("startup failed", exc_info=True)
        database.close_db()
        return 1

    gc.collect()
    gc.freeze()
    osenv.trim_memory()

    try:
        return int(qapp.exec())
    finally:
        app.shutdown()
        database.close_db()
        instance.close()


class Application:
    """Owns the long-lived objects and the on-demand main window (§10.1)."""

    def __init__(self, qapp: Any, minimized: bool = False) -> None:
        from . import notify, styles
        from .pomodoro import PomodoroController
        from .store import Store
        from .ui_overlay import OverlayWidget

        self.qapp = qapp
        self.minimized = minimized

        self.store = Store()
        self.controller = PomodoroController(
            focus_minutes=_minutes(self.store.setting("pomodoro_focus_min"), 25),
            break_minutes=_minutes(self.store.setting("pomodoro_break_min"), 5),
            auto_start=self.store.setting("pomodoro_auto_start", "0") == "1",
        )

        styles.apply(qapp)
        qapp.setWindowIcon(styles.app_icon())

        self.overlay: Optional[OverlayWidget] = None
        if self.store.setting("overlay_enabled", "1") == "1":
            self.overlay = self._new_overlay()

        self.window: Optional[Any] = None
        self.tray: Optional[Any] = None
        self.reminders: Optional[Any] = None

        self._build_tray()
        # Resolved after the tray exists so the tray tier can win (§11.3).
        self.notifier = notify.Notifier(tray=self.tray, overlay=self.overlay)
        self.controller.completed.connect(self._on_phase_completed)
        self.reminders = self._build_reminders()

    # --- startup ----------------------------------------------------------

    def start(self) -> None:
        """Finish startup: reveal the overlay and (unless minimized) the window."""
        if self.overlay is not None:
            self.overlay.show()  # showEvent restores the saved position (§9.4)
        if not self.minimized:
            self.open_main_window()
        _logger.info("started (minimized=%s)", self.minimized)

    def shutdown(self) -> None:
        """Flush the debounced overlay position before the process exits."""
        if self.overlay is not None:
            try:
                self.overlay.flush_position()
            except Exception:  # noqa: BLE001 - never block exit
                _logger.warning("could not persist overlay position", exc_info=True)
        try:
            self.store.flush()
        except Exception:  # noqa: BLE001
            _logger.warning("could not flush store", exc_info=True)

    # --- surfaces ---------------------------------------------------------

    def _new_overlay(self):
        from .ui_overlay import OverlayWidget

        overlay = OverlayWidget(self.store, self.controller)
        overlay.open_requested.connect(self.open_main_window)
        overlay.quit_requested.connect(self.quit)
        return overlay

    def open_main_window(self) -> None:
        """Create the main window lazily, or raise the existing one (§10.1)."""
        window = self.window
        if window is None:
            from .ui_main import MainWindow

            window = MainWindow(self.store, self.controller, self.overlay)
            window.destroyed.connect(self._on_window_destroyed)
            self.window = window
        window.show()
        window.raise_()
        window.activateWindow()

    def toggle_main_window(self) -> None:
        window = self.window
        if window is not None and window.isVisible():
            # Drop the reference first: WA_DeleteOnClose only deletes once the
            # event loop spins, and re-showing a doomed widget would be a bug.
            self.window = None
            window.close()
        else:
            self.open_main_window()

    def toggle_overlay(self) -> None:
        if self.overlay is None:
            self.overlay = self._new_overlay()
            self.notifier.set_overlay(self.overlay)
        self.overlay.toggle_visibility()

    def quit(self) -> None:
        self.qapp.quit()

    # --- tray (§13.1) -----------------------------------------------------

    def _build_tray(self) -> None:
        from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

        if not QSystemTrayIcon.isSystemTrayAvailable():
            _logger.warning("no system tray; overlay context menu is the only surface")
            return

        from . import styles

        tray = QSystemTrayIcon(styles.app_icon(), self.qapp)
        tray.setToolTip("Minimalist Goal Tracker")

        menu = QMenu()
        open_action = menu.addAction("Open Main Window")
        overlay_action = menu.addAction("Toggle Overlay")
        menu.addSeparator()
        exit_action = menu.addAction("Exit")
        open_action.triggered.connect(self.open_main_window)
        overlay_action.triggered.connect(self.toggle_overlay)
        exit_action.triggered.connect(self.quit)
        tray.setContextMenu(menu)

        # Convenience only: some AppIndicator hosts never emit Trigger.
        tray.activated.connect(self._on_tray_activated)

        tray.show()
        self.tray = tray
        self._tray_menu = menu  # keep the menu (and its actions) alive

    def _on_tray_activated(self, reason: Any) -> None:
        from PyQt6.QtWidgets import QSystemTrayIcon

        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_main_window()

    # --- reminders (§11) --------------------------------------------------

    def _build_reminders(self):
        if self.store.setting("reminders_enabled", "1") != "1":
            return None
        from .reminders import ReminderScheduler

        # The midnight/rollover timer lives inside Store (§13 step 9).
        return ReminderScheduler(
            self.notifier, self.controller, top_title=self._top_title
        )

    def _top_title(self) -> Optional[str]:
        titles = self.store.top_goal_titles(1)
        return titles[0] if titles else None

    # --- notifications on phase completion --------------------------------

    def _on_phase_completed(self, finished: str) -> None:
        """Announce the ended phase and what happens next (§11.3 tiers)."""
        from . import notify
        from .pomodoro import BREAK, FOCUS

        labels = {FOCUS: "Focus", BREAK: "Break"}
        next_mode = BREAK if finished == FOCUS else FOCUS
        verb = "started" if self.controller.auto_start else "ready"
        self.notifier.notify(
            notify.TITLE,
            f"{labels[finished]} session complete — {labels[next_mode]} {verb}",
        )

    def _on_window_destroyed(self, *_args: Any) -> None:
        self.window = None


def _minutes(raw: str, fallback: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return fallback


# --- diagnostics (§13.2) --------------------------------------------------


def _doctor() -> int:
    """Print environment diagnostics and exit. Creates no visible windows."""
    import shutil

    print(f"MGT doctor ({VERSION})")
    print(f"  OS                : {sys.platform}")
    print(f"  WSL               : {osenv.IS_WSL}")
    print(f"  session type      : {os.environ.get('XDG_SESSION_TYPE', '-')}")
    print(f"  QT_QPA_PLATFORM   : {os.environ.get('QT_QPA_PLATFORM', '-')}")

    app = None
    try:
        from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR

        print(f"  Qt version        : {QT_VERSION_STR}")
        print(f"  PyQt version      : {PYQT_VERSION_STR}")

        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        print(f"  Qt platform       : {app.platformName()}")
    except Exception as exc:  # noqa: BLE001 - doctor must never crash
        print(f"  Qt                : unavailable ({exc})")

    tray = False
    if app is not None:
        from PyQt6.QtGui import QGuiApplication

        for screen in QGuiApplication.screens():
            g = screen.geometry()
            a = screen.availableGeometry()
            print(
                f"  screen {screen.name()!r}: geom={g.width()}x{g.height()} "
                f"avail={a.width()}x{a.height()} "
                f"dpr={screen.devicePixelRatio()} dpi={screen.logicalDotsPerInch()}"
            )
        try:
            from PyQt6.QtWidgets import QSystemTrayIcon

            tray = QSystemTrayIcon.isSystemTrayAvailable()
        except Exception:
            tray = False
    print(f"  tray available    : {tray}")

    print(f"  notification      : {_notification_backend()}")
    print(f"  data dir          : {osenv.data_dir()}")
    print(f"  DB journal mode   : {_db_journal_mode()}")
    print(f"  libxcb-cursor0    : {_has_lib('xcb-cursor')}")
    print(f"  notify-send       : {shutil.which('notify-send') or 'not found'}")

    cwd = Path.cwd()
    if str(cwd).startswith("/mnt/"):
        print(
            f"  WARNING: working directory is under /mnt/ ({cwd}); "
            "keep the repo and data dir on the Linux filesystem."
        )
    return 0


def _notification_backend() -> str:
    """Resolve the backend the app would pick, without creating a tray."""
    try:
        from . import notify

        return notify.Notifier().backend_name
    except Exception as exc:  # noqa: BLE001
        return f"unavailable ({exc})"


def _db_journal_mode() -> str:
    try:
        from . import database

        database.init_db()
        mode = database.journal_mode()
        database.close_db()
        return mode
    except Exception as exc:  # noqa: BLE001
        return f"unavailable ({exc})"


def _has_lib(name: str) -> bool:
    try:
        from ctypes.util import find_library

        return bool(find_library(name))
    except Exception:
        return False
