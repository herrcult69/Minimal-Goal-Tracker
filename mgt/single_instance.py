"""Single-instance guard via ``QLocalServer``/``QLocalSocket`` (§4.4).

Autostart plus a manual launch must never produce two overlays. The second
process connects to the first, sends ``show``, and exits 0; the first raises
``show_requested`` so the app can open (and raise) its main window.
"""

from __future__ import annotations

import getpass
import re
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

_CONNECT_TIMEOUT_MS = 300
_SHOW = b"show"
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


def server_name() -> str:
    """Per-user name, sanitized: ``DOMAIN\\user`` is not a valid socket name."""
    try:
        user = _UNSAFE.sub("_", getpass.getuser())
    except Exception:  # no username available (rare in containers)
        user = ""
    # An all-unsafe username sanitizes to "", which would leave a bare "mgt-".
    return f"mgt-{user}" if user else "mgt-default"


class SingleInstance(QObject):
    """Listener side: owns the ``QLocalServer`` for this user."""

    #: Another launch asked us to come to the foreground.
    show_requested = pyqtSignal()

    def __init__(self, name: Optional[str] = None, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.name = name or server_name()
        self._server: Optional[QLocalServer] = None

    # --- acquisition -------------------------------------------------------

    def try_acquire(self) -> bool:
        """``True`` when this process is the first one; ``False`` if another runs."""
        socket = QLocalSocket()
        socket.connectToServer(self.name)
        if socket.waitForConnected(_CONNECT_TIMEOUT_MS):
            socket.write(_SHOW)
            socket.flush()
            socket.waitForBytesWritten(_CONNECT_TIMEOUT_MS)
            socket.disconnectFromServer()
            if socket.state() != QLocalSocket.LocalSocketState.UnconnectedState:
                socket.waitForDisconnected(_CONNECT_TIMEOUT_MS)
            return False

        # No live server: any leftover socket file from a crash is stale.
        QLocalServer.removeServer(self.name)

        server = QLocalServer(self)
        server.newConnection.connect(self._on_new_connection)
        if not server.listen(self.name):
            # Another process won the race between removeServer and listen.
            QLocalServer.removeServer(self.name)
            if not server.listen(self.name):
                return False
        self._server = server
        return True

    def _on_new_connection(self) -> None:
        if self._server is None:
            return
        while self._server.hasPendingConnections():
            connection = self._server.nextPendingConnection()
            if connection is None:
                break
            connection.readyRead.connect(
                lambda conn=connection: self._on_ready_read(conn)
            )
            connection.disconnected.connect(connection.deleteLater)
            # A client that connects and sends nothing still means "show".
            self.show_requested.emit()

    @staticmethod
    def _on_ready_read(connection: QLocalSocket) -> None:
        connection.readAll()  # payload is ignored; the connection itself is the signal

    # --- teardown ----------------------------------------------------------

    def close(self) -> None:
        """Stop listening and drop the socket file so the next launch is clean."""
        if self._server is None:
            return
        self._server.close()
        QLocalServer.removeServer(self.name)
        self._server = None


def acquire(name: Optional[str] = None, parent: Optional[QObject] = None) -> Optional[SingleInstance]:
    """Return a listener, or ``None`` when another instance was signalled.

    The caller exits 0 on ``None``; on success it connects
    ``instance.show_requested`` to ``app.open_main_window``.
    """
    instance = SingleInstance(name, parent)
    return instance if instance.try_acquire() else None
