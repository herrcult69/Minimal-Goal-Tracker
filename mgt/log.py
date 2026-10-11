"""Rotating file logging and Qt message routing.

Qt-free at import time; the Qt message handler is imported lazily so this
module can be imported before a QApplication exists (and without Qt installed).
Under ``pythonw.exe`` stdout/stderr are None, so a file log is the only way to
see a silent-boot crash. No StreamHandler is ever attached.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from . import osenv

_logger = logging.getLogger("mgt")
_configured = False


def setup() -> logging.Logger:
    """Attach the rotating file handler, install the excepthook. Idempotent."""
    global _configured
    if _configured:
        return _logger

    level = logging.DEBUG if os.environ.get("MGT_DEBUG") else logging.WARNING
    handler = RotatingFileHandler(
        osenv.data_dir() / "mgt.log",
        maxBytes=256 * 1024,
        backupCount=1,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    _logger.setLevel(level)
    _logger.addHandler(handler)
    _logger.propagate = False

    sys.excepthook = _excepthook
    _configured = True
    return _logger


def _excepthook(exc_type, exc_value, exc_tb) -> None:
    _logger.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_tb))


def install_qt_message_handler() -> None:
    """Route Qt messages (QSS parse errors, etc.) into the log instead of a console."""
    try:
        from PyQt6.QtCore import QtMsgType, qInstallMessageHandler
    except Exception:
        return

    levels = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }

    def _handler(mode, context, message):  # noqa: ANN001 - Qt callback signature
        _logger.log(levels.get(mode, logging.WARNING), "Qt: %s", message)

    qInstallMessageHandler(_handler)
