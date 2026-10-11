"""Notification backend resolution and the four-tier fallback chain (§11.3).

Everything is injected, so these run without a tray, a display, or
``notify-send`` — the only Qt touched is the MessageIcon enum import.
"""

from mgt import notify
from mgt.notify import TITLE, Notifier


class FakeTray:
    def __init__(self, visible=True, supports=True):
        self._visible = visible
        self._supports = supports
        self.messages = []

    def isVisible(self):
        return self._visible

    def supportsMessages(self):
        return self._supports

    def showMessage(self, title, body, icon, msecs):
        self.messages.append((title, body, msecs))


class FakeOverlay:
    def __init__(self):
        self.banners = []

    def show_banner(self, text):
        self.banners.append(text)


def make(**kw):
    base = dict(spawn=lambda *a: True, have_notify_send=False, toast_enabled=False)
    base.update(kw)
    return Notifier(**base)


def test_tray_wins_when_visible_and_supports_messages():
    tray = FakeTray()
    n = make(tray=tray, overlay=FakeOverlay())
    assert n.backend_name == "tray"
    assert n.notify("t", "b") is True
    assert tray.messages == [("t", "b", 8000)]


def test_tray_ignored_when_hidden_or_unsupporting():
    overlay = FakeOverlay()
    assert make(tray=FakeTray(visible=False), overlay=overlay).backend_name == "banner"
    assert make(tray=FakeTray(supports=False), overlay=overlay).backend_name == "banner"


def test_notify_send_second_tier_and_arguments():
    calls = []
    n = make(spawn=lambda prog, args: calls.append((prog, args)) or True,
             have_notify_send=True, overlay=FakeOverlay())
    assert n.backend_name == "notify-send"
    assert n.notify(TITLE, "body") is True
    assert calls == [("notify-send", ["--app-name=MGT", "--", TITLE, "body"])]


def test_notify_send_failure_falls_through_to_banner():
    overlay = FakeOverlay()
    n = make(spawn=lambda *a: False, have_notify_send=True, overlay=overlay)
    assert n.backend_name == "notify-send"
    assert n.notify("t", "b") is True
    assert overlay.banners == ["b"]


def test_banner_third_tier_when_no_tray_or_notify_send():
    overlay = FakeOverlay()
    n = make(overlay=overlay, toast_send=lambda *a: True, toast_enabled=True)
    assert n.backend_name == "banner"  # banner outranks the optional toast
    assert n.notify("t", "b") is True
    assert overlay.banners == ["b"]


def test_toast_only_when_enabled_and_nothing_else_available():
    sent = []
    n = make(toast_enabled=True, toast_send=lambda t, b: sent.append((t, b)) or True)
    assert n.backend_name == "toast"
    assert n.notify("t", "b") is True
    assert sent == [("t", "b")]


def test_toast_disabled_without_env_flag():
    assert make(toast_enabled=False, toast_send=lambda *a: True).backend_name == "none"


def test_none_when_nothing_available():
    n = make()
    assert n.backend_name == "none"
    assert n.notify("t", "b") is False


def test_module_backend_name_tracks_last_notifier():
    make(tray=FakeTray())
    assert notify.backend_name == "tray"
    make()
    assert notify.backend_name == "none"


def test_broken_tray_is_tolerated():
    class Boom(FakeTray):
        def isVisible(self):
            raise RuntimeError("no display")

    assert make(tray=Boom(), overlay=FakeOverlay()).backend_name == "banner"
