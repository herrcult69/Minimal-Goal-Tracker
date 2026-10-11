"""Headless GUI smoke test for both windows (IMPLEMENTATION.md §16.1).

Runs on Windows, Ubuntu, and WSL with no display server: the module pins
``QT_QPA_PLATFORM=offscreen`` before PyQt6 is imported, then builds the overlay
and the main window with 0, 1, 6, and 50 tasks with the stylesheet applied.
Qt's own messages are captured through ``qInstallMessageHandler`` so a QSS
parse error becomes a test failure instead of console noise. It asserts:

1. construction and repeated ``goals_changed`` emissions never raise;
2. no stylesheet / QSS warning is emitted (unsupported properties, bad selectors,
   ``pt <= 0`` font sizes);
3. the overlay renders with the same size across repeated emissions and never
   shows more than ``MAX_ROWS + 1`` rows.
"""

from __future__ import annotations

import os

# Must be set before any PyQt6 import so QApplication picks the plugin, and it
# must survive a plain ``uv run pytest`` (no env var supplied by the caller).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QtMsgType, qInstallMessageHandler
from PyQt6.QtWidgets import QApplication

from mgt import database, styles
from mgt.pomodoro import PomodoroController
from mgt.store import Store
from mgt.ui_main import MainWindow
from mgt.ui_overlay import MAX_ROWS, WIDTH, OverlayWidget, OverlayRowWidgets

TASK_COUNTS = (0, 1, 6, 50)

#: Substrings that mark a QSS problem rather than an unrelated Qt warning
#: (offscreen still emits e.g. QStandardPaths notices on some systems).
_QSS_MARKERS = ("stylesheet", "could not parse", "unknown property")


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    styles.apply(app)
    yield app


@pytest.fixture()
def qt_messages(qapp):
    """Capture every Qt message emitted while the test body runs."""
    messages = []
    previous = qInstallMessageHandler(
        lambda mode, context, message: messages.append((mode, message))
    )
    try:
        yield messages
    finally:
        qInstallMessageHandler(previous)


@pytest.fixture()
def store(qapp, tmp_path):
    """A Store on a throwaway database, so real data is never touched."""
    database.init_db(tmp_path / "goals.db")
    try:
        yield Store()
    finally:
        database.close_db()


def _visible_rows(overlay: OverlayWidget) -> int:
    """Rows the user would see: title label pool plus the ``+N more`` row."""
    pool = sum(1 for pair in overlay._row_pairs if pair.title_lbl.isVisibleTo(overlay))
    more = 1 if overlay._more_row.isVisibleTo(overlay) else 0
    return pool + more


def _assert_no_qss_warnings(messages) -> None:
    qss = [
        message
        for mode, message in messages
        if mode in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg)
        and any(marker in message.lower() for marker in _QSS_MARKERS)
    ]
    assert not qss, f"stylesheet warnings emitted: {qss}"


@pytest.mark.parametrize("count", TASK_COUNTS)
def test_smoke_both_windows(qapp, store, qt_messages, count):
    for index in range(count):
        store.add_goal(
            f"Task {index:02d} with a deliberately long title to force elision", "today"
        )
    store.flush()

    controller = PomodoroController()
    overlay = OverlayWidget(store, controller)
    window = MainWindow(store, controller, overlay)
    overlay.show()
    window.show()
    qapp.processEvents()

    # Width is a hard constant; height must not drift across repeated emissions.
    sizes = set()
    for _ in range(3):
        store.goals_changed.emit()
        overlay.refresh()
        qapp.processEvents()
        sizes.add((overlay.width(), overlay.height()))

    assert sizes == {(WIDTH, overlay.height())}, f"overlay size drifted: {sizes}"
    assert overlay.width() == WIDTH

    expected = _expected_rows(count)
    visible = _visible_rows(overlay)
    assert visible == expected, f"{count} tasks -> {visible} rows, expected {expected}"
    assert visible <= MAX_ROWS + 1

    assert not window.isHidden(), "main window failed to show"
    _assert_no_qss_warnings(qt_messages)

    window.close()
    overlay.close()
    qapp.processEvents()


def _expected_rows(count: int) -> int:
    """Rows the overlay shows: empty placeholder, N tasks, or MAX_ROWS + "+N more"."""
    if count == 0:
        return 1  # "Nothing planned for today"
    return min(count, MAX_ROWS) + (1 if count > MAX_ROWS else 0)


@pytest.mark.parametrize("count", TASK_COUNTS)
def test_smoke_no_qss_warnings_when_styled(qapp, store, qt_messages, count):
    """Constructing styled widgets must not produce any QSS diagnostic."""
    for index in range(count):
        store.add_goal(f"Styled task {index}", "today")
    store.flush()

    controller = PomodoroController()
    overlay = OverlayWidget(store, controller)
    window = MainWindow(store, controller, overlay)
    overlay.refresh()
    qapp.processEvents()

    _assert_no_qss_warnings(qt_messages)
    window.close()
    overlay.close()
    qapp.processEvents()
