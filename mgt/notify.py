"""Notification dispatch with a four-tier fallback (IMPLEMENTATION.md §11.3).

The backend is resolved **once** at startup, first available wins:

1. tray       — ``QSystemTrayIcon.showMessage`` (native toast / D-Bus), when the
                tray is visible and ``supportsMessages()``.
2. notify-send — ``QProcess.startDetached`` (non-blocking, no shell, no zombie).
3. banner     — the overlay's banner row (always works, even under WSLg).
4. toast      — optional lazy ``win11toast`` (only with the ``toast`` extra and
                ``MGT_TOAST=1``), because it pulls in heavy winrt modules.

All collaborators are injectable so dispatch is unit-testable without Qt, a
tray, a display, or ``notify-send`` on the machine running the tests.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
from typing import Callable, Optional

TITLE = "Minimalist Goal Tracker"
BANNER_MS = 20_000
_TOAST_ENV = "MGT_TOAST"

#: Name of the backend selected by the most recent :class:`Notifier`. Read by
#: ``mgt.app --doctor`` (§13.2); ``"unresolved"`` until a Notifier is built.
backend_name = "unresolved"

Spawn = Callable[[str, list], bool]
ToastSend = Callable[[str, str], bool]


def _qprocess_spawn(program: str, args: list) -> bool:
    """Non-blocking detached launch via QProcess (imported lazily, no shell)."""
    from PyQt6.QtCore import QProcess

    result = QProcess.startDetached(program, args)
    if isinstance(result, tuple):  # PyQt6 returns (started, pid)
        return bool(result[0])
    return bool(result)


def _has_win11toast() -> bool:
    try:
        return importlib.util.find_spec("win11toast") is not None
    except Exception:  # noqa: BLE001 - find_spec may raise on odd installations
        return False


def _win11toast_send(title: str, body: str) -> bool:
    """Lazy import of the optional, heavy winrt-backed toast library."""
    import win11toast  # noqa: PLC0415 - deliberately lazy

    win11toast.notify(title, body)
    return True


class Notifier:
    """Resolve one notification backend and dispatch through the fallback chain."""

    def __init__(
        self,
        tray: Optional[object] = None,
        overlay: Optional[object] = None,
        spawn: Optional[Spawn] = None,
        toast_send: Optional[ToastSend] = None,
        have_notify_send: Optional[bool] = None,
        toast_enabled: Optional[bool] = None,
    ) -> None:
        self._tray = tray
        self._overlay = overlay
        self._spawn = spawn or _qprocess_spawn
        self._toast_send = toast_send
        self._have_notify_send = (
            shutil.which("notify-send") is not None
            if have_notify_send is None
            else bool(have_notify_send)
        )
        self._toast_enabled = (
            os.environ.get(_TOAST_ENV) == "1"
            if toast_enabled is None
            else bool(toast_enabled)
        )
        self.backend_name = self._resolve()

        global backend_name
        backend_name = self.backend_name

    # --- resolution -------------------------------------------------------

    def set_overlay(self, overlay: Optional[object]) -> None:
        """Late-bind the overlay: the tray may create it after startup (§13.1)."""
        self._overlay = overlay
        if self.backend_name == "none" and self._resolve() != "none":
            self.backend_name = self._resolve()
            global backend_name
            backend_name = self.backend_name

    def _tray_ok(self) -> bool:
        tray = self._tray
        if tray is None:
            return False
        try:
            return bool(tray.isVisible()) and bool(tray.supportsMessages())
        except Exception:  # noqa: BLE001 - a broken tray must never crash startup
            return False

    def _toast_ok(self) -> bool:
        if not self._toast_enabled:
            return False
        return self._toast_send is not None or _has_win11toast()

    def _resolve(self) -> str:
        if self._tray_ok():
            return "tray"
        if self._have_notify_send:
            return "notify-send"
        if self._overlay is not None:
            return "banner"
        if self._toast_ok():
            return "toast"
        return "none"

    # --- dispatch ---------------------------------------------------------

    def notify(self, title: str, body: str) -> bool:
        """Deliver a message through the first tier that succeeds.

        The backend is picked once, but a tier that fails at call time falls
        through to the next, so a transient tray failure still gets delivered.
        """
        for attempt in (
            self._try_tray,
            self._try_notify_send,
            self._try_banner,
            self._try_toast,
        ):
            try:
                if attempt(title, body):
                    return True
            except Exception:  # noqa: BLE001 - one bad tier must not stop the rest
                continue
        return False

    def _try_tray(self, title: str, body: str) -> bool:
        if not self._tray_ok():
            return False
        from PyQt6.QtWidgets import QSystemTrayIcon

        self._tray.showMessage(title, body, QSystemTrayIcon.MessageIcon.NoIcon, 8000)
        return True

    def _try_notify_send(self, title: str, body: str) -> bool:
        if not self._have_notify_send:
            return False
        return bool(
            self._spawn("notify-send", ["--app-name=MGT", "--", title, body])
        )

    def _try_banner(self, title: str, body: str) -> bool:
        if self._overlay is None:
            return False
        # The overlay banner is 300 px wide, so the title is dropped there.
        self._overlay.show_banner(body)
        return True

    def _try_toast(self, title: str, body: str) -> bool:
        if not self._toast_ok():
            return False
        send = self._toast_send or _win11toast_send
        return bool(send(title, body))
