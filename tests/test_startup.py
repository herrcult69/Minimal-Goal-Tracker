"""Autostart enable/disable per OS (§12), Qt-free.

The running interpreter is WSL, where autostart is deliberately unsupported,
so the Linux and Windows branches are exercised by patching the platform flags
and ``desktop_path()`` rather than by touching the real system.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from mgt import osenv, startup


@pytest.fixture
def linux(tmp_path, monkeypatch):
    """Pretend to be native Ubuntu with a throwaway autostart directory."""
    monkeypatch.setattr(osenv, "IS_WSL", False)
    monkeypatch.setattr(osenv, "IS_WINDOWS", False)
    monkeypatch.setattr(osenv, "IS_LINUX", True)
    monkeypatch.setattr(startup, "_IS_LINUX", True)
    target = tmp_path / "autostart" / "mgt.desktop"
    monkeypatch.setattr(startup, "desktop_path", lambda: target)
    return target


# --- absolute paths --------------------------------------------------------

def test_paths_are_absolute_and_never_cwd():
    assert startup.script_path().is_absolute()
    assert startup.script_path().name == "main.py"
    assert startup.script_path().exists(), "autostart must point at a real main.py"
    assert startup.python_path().is_absolute()

    # Regression guard for §12: resolving via cwd is undefined at login time.
    argv = startup.command()
    assert argv[0] == str(startup.python_path())
    assert argv[1] == str(startup.script_path())
    assert argv[-1] == "--minimized"
    assert all(Path(part).is_absolute() for part in argv[:2])


# --- quoting ---------------------------------------------------------------

def test_desktop_quote_leaves_plain_paths_alone():
    assert startup.desktop_quote("/usr/bin/python3") == "/usr/bin/python3"


def test_desktop_quote_uses_double_quotes_for_spaces():
    # Single quotes are not special in .desktop Exec lines, so a spaced venv
    # path must come out double-quoted or log-in silently fails.
    assert startup.desktop_quote("/home/a b/python") == '"/home/a b/python"'
    assert startup.desktop_quote('/home/"q"/x') == '"/home/\\"q\\"/x"'
    assert startup.desktop_quote("$HOME/x") == '"\\$HOME/x"'


# --- support matrix --------------------------------------------------------

def test_wsl_is_unsupported(monkeypatch):
    monkeypatch.setattr(osenv, "IS_WSL", True)
    assert startup.is_supported() is False
    assert "WSL" in startup.unsupported_reason()
    assert "not supported" in startup.status_line().lower()
    with pytest.raises(RuntimeError):
        startup.enable()
    with pytest.raises(RuntimeError):
        startup.disable()


def test_linux_is_supported(linux):
    assert startup.is_supported() is True


# --- enable / disable / status --------------------------------------------

def test_enable_then_disable_roundtrip(linux):
    assert startup.is_enabled() is False
    startup.enable()

    text = linux.read_text(encoding="utf-8")
    assert "Type=Application" in text
    assert "Name=MGT" in text
    assert "X-GNOME-Autostart-Delay=5" in text
    assert "NoDisplay=true" in text

    exec_line = next(l for l in text.splitlines() if l.startswith("Exec="))
    assert exec_line.startswith("Exec=env MALLOC_ARENA_MAX=2 ")
    # Compare against the desktop-escaped form: on Windows the raw str() has
    # backslashes that desktop_quote() doubles, so the plain path won't appear.
    assert startup.desktop_quote(str(startup.python_path())) in exec_line
    assert startup.desktop_quote(str(startup.script_path())) in exec_line
    assert exec_line.endswith("--minimized")

    assert startup.is_enabled() is True
    assert "enabled" in startup.status_line()

    startup.disable()
    assert startup.is_enabled() is False
    assert "disabled" in startup.status_line()

    startup.disable()  # idempotent: a second disable must not raise


def test_enable_is_idempotent(linux):
    startup.enable()
    first = linux.read_text(encoding="utf-8")
    startup.enable()
    assert linux.read_text(encoding="utf-8") == first


# --- CLI -------------------------------------------------------------------

def test_cli_status_on_wsl(monkeypatch, capsys):
    monkeypatch.setattr(osenv, "IS_WSL", True)
    assert startup.main(["--status"]) == 0
    assert "autostart:" in capsys.readouterr().out


def test_cli_enable_and_disable(linux, capsys):
    assert startup.main(["--enable"]) == 0
    assert linux.exists()
    assert "enabled" in capsys.readouterr().out

    assert startup.main(["--disable"]) == 0
    assert not linux.exists()
    assert "disabled" in capsys.readouterr().out


def test_cli_without_action_prints_usage(capsys):
    assert startup.main([]) == 2
    assert "usage" in capsys.readouterr().out


def test_cli_reports_failure_without_traceback(monkeypatch, capsys):
    monkeypatch.setattr(osenv, "IS_WSL", True)
    assert startup.main(["--enable"]) == 1
    out = capsys.readouterr().out
    assert "failed" in out
    assert "Traceback" not in out


def test_module_is_runnable_as_script():
    # §12 documents `python -m mgt.startup --status` as the operator entry point.
    repo = Path(startup.__file__).resolve().parent.parent
    proc = subprocess.run(
        [sys.executable, "-m", "mgt.startup", "--status"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("autostart:")
