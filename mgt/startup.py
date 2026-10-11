"""Autostart enable/disable per OS (IMPLEMENTATION.md §12). Qt-free.

Three mechanisms, no subprocess and no ``.vbs`` wrapper: a ``HKCU\\...\\Run``
value on Windows, an XDG ``.desktop`` file on Linux, and an explicit "not
supported" on WSL (there is no real login session to hook into).

Every path is absolute and derived from ``__file__`` / ``sys.executable`` —
never from the current directory, which is undefined at login time.

CLI: ``python -m mgt.startup --enable | --disable | --status``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional, Sequence

from . import osenv

APP_NAME = "MGT"
VALUE_NAME = "MGT"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

#: ``MALLOC_ARENA_MAX=2`` keeps glibc from mapping a 64 MB arena per thread,
#: which matters for the memory budget in §16.3 on a long-lived tray process.
DESKTOP_ENV = ("env", "MALLOC_ARENA_MAX=2")

_IS_LINUX = osenv.IS_LINUX and not osenv.IS_WSL


def script_path() -> Path:
    """Absolute path to ``main.py`` (repo root, two levels above this file)."""
    return Path(__file__).resolve().parent.parent / "main.py"


def python_path() -> Path:
    """``pythonw.exe`` on Windows (no console flash), else the running python."""
    executable = Path(sys.executable)
    if osenv.IS_WINDOWS:
        windowed = executable.with_name("pythonw.exe")
        if windowed.exists():
            return windowed
    return executable


def command() -> List[str]:
    """The argv used by every autostart mechanism."""
    return [str(python_path()), str(script_path()), "--minimized"]


def desktop_path() -> Path:
    return Path.home() / ".config" / "autostart" / f"{APP_NAME.lower()}.desktop"


def desktop_quote(part: str) -> str:
    """Quote one ``Exec`` argument per the freedesktop Desktop Entry spec.

    The spec only understands double quotes (reserved characters escaped with
    a backslash) — ``shlex.quote`` emits single quotes, which are *not*
    special there, so a venv path containing a space would break at login.
    """
    if part and not any(ch in part for ch in ' \t\n"\'\\><~|&;$*?#()`'):
        return part
    escaped = part.replace("\\", "\\\\")
    for reserved in ('"', "`", "$"):
        escaped = escaped.replace(reserved, "\\" + reserved)
    return f'"{escaped}"'


def is_supported() -> bool:
    """WSL has no login session; other platforms are handled natively."""
    if osenv.IS_WSL:
        return False
    return osenv.IS_WINDOWS or _IS_LINUX


def unsupported_reason() -> str:
    if osenv.IS_WSL:
        return "Not supported under WSL"
    return "Not supported on this platform"


# --- Windows ---------------------------------------------------------------

def _winreg():
    import winreg

    return winreg


def _windows_vbs_path() -> Path:
    """Path to the silent VBS launcher file in the app's data directory."""
    return osenv.data_dir() / "launch.vbs"


def _windows_vbs_content() -> str:
    """Generate VBS script that runs the command with window style 0 (hidden)."""
    import subprocess

    # Build the command line exactly as the shell would see it.
    cmd = subprocess.list2cmdline(command())
    # Escape double quotes for VBS string literal: double each double quote.
    cmd_escaped = cmd.replace('"', '""')
    return f'''\
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "{cmd_escaped}", 0, False
'''


def _windows_value() -> Optional[str]:
    winreg = _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, VALUE_NAME)
            return str(value)
    except FileNotFoundError:
        return None
    except OSError:
        return None


# --- public API ------------------------------------------------------------

def is_enabled() -> bool:
    if not is_supported():
        return False
    if osenv.IS_WINDOWS:
        return _windows_value() is not None
    return desktop_path().exists()


def enable() -> None:
    """Idempotent: writing the same command twice is harmless."""
    if not is_supported():
        raise RuntimeError(unsupported_reason())

    if osenv.IS_WINDOWS:
        winreg = _winreg()
        import subprocess

        # Generate a silent VBS launcher so no terminal flashes at system boot.
        vbs_path = _windows_vbs_path()
        vbs_path.parent.mkdir(parents=True, exist_ok=True)
        vbs_path.write_text(_windows_vbs_content(), encoding="utf-8-sig")

        # Register wscript.exe //B with the VBS launcher.
        # //B runs in batch mode (no alerts), window style 0 ensures no console.
        data = subprocess.list2cmdline(["wscript.exe", "//B", str(vbs_path)])
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, data)
        return

    argv = DESKTOP_ENV + tuple(desktop_quote(part) for part in command())
    path = desktop_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        "Comment=Minimalist Goal Tracker\n"
        f"Exec={' '.join(argv)}\n"
        "X-GNOME-Autostart-Delay=5\n"
        "NoDisplay=true\n",
        encoding="utf-8",
    )


def disable() -> None:
    if not is_supported():
        raise RuntimeError(unsupported_reason())

    if osenv.IS_WINDOWS:
        winreg = _winreg()
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, VALUE_NAME)
        except FileNotFoundError:
            pass
        return

    desktop_path().unlink(missing_ok=True)


def status_line() -> str:
    if not is_supported():
        return f"autostart: {unsupported_reason().lower()}"
    state = "enabled" if is_enabled() else "disabled"
    if osenv.IS_WINDOWS:
        detail = _windows_value() or "-"
    else:
        detail = str(desktop_path()) if is_enabled() else "-"
    return f"autostart: {state} ({detail})"


# --- CLI -------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    if "--status" in args:
        print(status_line())
        return 0

    wanted = None
    if "--enable" in args:
        wanted = True
    elif "--disable" in args:
        wanted = False

    if wanted is None:
        print("usage: python -m mgt.startup --enable | --disable | --status")
        return 2

    try:
        enable() if wanted else disable()
    except Exception as exc:  # noqa: BLE001 - CLI reports, never tracebacks
        print(f"autostart: failed ({exc})")
        return 1

    print(status_line())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
