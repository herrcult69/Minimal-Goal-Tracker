"""Single-instance handshake (§4.4), exercised against a real QLocalServer.

No display needed: ``QLocalSocket``/``QLocalServer`` only require a
``QCoreApplication``. Each test uses its own socket name so a stale socket
from a previous run can never interfere.
"""

from __future__ import annotations

import uuid

import pytest
from PyQt6.QtCore import QCoreApplication

from mgt import single_instance
from mgt.single_instance import SingleInstance, acquire, server_name


@pytest.fixture(scope="module", autouse=True)
def qapp():
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


@pytest.fixture
def name():
    return f"mgt-test-{uuid.uuid4().hex[:12]}"


@pytest.fixture
def listener(name):
    instance = SingleInstance(name)
    assert instance.try_acquire() is True
    yield instance
    instance.close()


# --- naming ----------------------------------------------------------------

def test_server_name_is_socket_safe():
    name = server_name()
    assert name.startswith("mgt-")
    # A DOMAIN\user or "user name" must not leak separators into the name.
    for ch in "\\/: \t\n":
        assert ch not in name


# --- handshake -------------------------------------------------------------

def test_first_process_wins_and_second_signals(listener):
    hits = []
    listener.show_requested.connect(lambda: hits.append(1))

    second = SingleInstance(listener.name)
    assert second.try_acquire() is False

    # newConnection is delivered by the event loop, which only runs inside
    # app.exec() in production — drain it here instead.
    for _ in range(50):
        QCoreApplication.processEvents()
        if hits:
            break

    # The connection itself is the signal; the payload is ignored.
    assert len(hits) == 1


def test_acquire_helper_returns_none_when_taken(listener):
    assert acquire(listener.name) is None


def test_acquire_helper_returns_listener_when_free(name):
    instance = acquire(name)
    assert instance is not None
    instance.close()


def test_close_releases_the_name(listener):
    name = listener.name
    listener.close()

    successor = SingleInstance(name)
    assert successor.try_acquire() is True
    successor.close()


def test_stale_socket_from_a_crash_is_reclaimed(name):
    # A crashed process leaves the socket file behind; removeServer() in
    # try_acquire() must clear it instead of wedging every later launch.
    from PyQt6.QtNetwork import QLocalServer

    stale = QLocalServer()
    assert stale.listen(name)
    stale.close()  # socket file now orphaned on disk

    instance = SingleInstance(name)
    assert instance.try_acquire() is True
    instance.close()


# --- teardown hygiene ------------------------------------------------------

def test_close_is_idempotent(name):
    instance = SingleInstance(name)
    instance.try_acquire()
    instance.close()
    instance.close()  # must not raise


def test_close_without_acquire_is_a_noop(name):
    SingleInstance(name).close()
