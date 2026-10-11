"""OS detection, Qt environment bootstrap, paths, and OS efficiency tweaks.

Qt-free by design: importable instantly and testable without a display.
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")
IS_WSL = IS_LINUX and bool(os.environ.get("WSL_DISTRO_NAME"))


def configure_qt_environment() -> None:
    """Call BEFORE a QApplication exists."""
    if IS_LINUX and "QT_QPA_PLATFORM" not in os.environ:
        pref = os.environ.get("MGT_QPA", "auto")  # auto | xcb | wayland
        if pref == "auto":
            # Native Wayland forbids client-side always-on-top and window
            # positioning. XWayland (xcb) supports both, so prefer it whenever
            # DISPLAY exists.
            pref = "xcb" if os.environ.get("DISPLAY") else "wayland"
        os.environ["QT_QPA_PLATFORM"] = pref


def data_dir() -> Path:
    override = os.environ.get("MGT_DATA_DIR")
    if override:
        p = Path(override)
    elif IS_WINDOWS:
        p = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "MGT"
    else:
        p = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "mgt"
    p.mkdir(parents=True, exist_ok=True)
    return p


def apply_efficiency_tweaks() -> None:
    """Best-effort OS-level power/priority tweaks. Never fatal."""
    try:
        if IS_WINDOWS:
            _tweaks_windows()
        elif IS_LINUX:
            os.nice(5)
    except Exception:
        pass


def _tweaks_windows() -> None:
    from ctypes import wintypes

    # AUMID must be set before any window for correct taskbar grouping and
    # toast attribution.
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "MGT.MinimalistGoalTracker"
        )
    except Exception:
        pass

    # Dismiss any console window allocated by Windows (e.g. from uv's console-subsystem
    # pythonw.exe or manual launch). This prevents a terminal flash at startup.
    try:
        ctypes.windll.kernel32.FreeConsole()
    except Exception:
        pass

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetCurrentProcess.restype = wintypes.HANDLE

    class _PTS(ctypes.Structure):
        _fields_ = [
            ("Version", wintypes.ULONG),
            ("ControlMask", wintypes.ULONG),
            ("StateMask", wintypes.ULONG),
        ]

    # EcoQoS (Windows 11 efficiency mode): version 1, EXECUTION_SPEED throttling on.
    k32.SetProcessInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    st = _PTS(1, 1, 1)
    k32.SetProcessInformation(
        k32.GetCurrentProcess(), 4, ctypes.byref(st), ctypes.sizeof(st)
    )  # 4 = ProcessPowerThrottling

    # BELOW_NORMAL priority class.
    k32.SetPriorityClass(k32.GetCurrentProcess(), 0x4000)


def trim_memory() -> None:
    """Return freed heap pages to the OS. Never fatal."""
    try:
        if IS_WINDOWS:
            from ctypes import wintypes

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            k32.SetProcessWorkingSetSize.argtypes = [
                wintypes.HANDLE,
                ctypes.c_size_t,
                ctypes.c_size_t,
            ]
            k32.SetProcessWorkingSetSize(
                k32.GetCurrentProcess(), ctypes.c_size_t(-1), ctypes.c_size_t(-1)
            )
        elif IS_LINUX:
            ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass
