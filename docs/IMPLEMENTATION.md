# Implementation Blueprint: Minimalist Goal Tracker (MGT) — v2

A complete, self-contained specification for autonomous coding agents to build **MGT**: a horizon-based goal tracker with an always-on-top read-only desktop overlay, a shared Pomodoro timer, and non-intrusive reminders.

**v2 targets:** Windows 10/11 native, Ubuntu 22.04/24.04 native (X11 and Wayland sessions), and WSL2 (WSLg), on single- and multi-monitor setups with mixed DPI, at the lowest practical CPU/RAM/battery cost.

---

## 0. What Changed From v1 (and Why)

| Area | v1 | v2 | Reason |
|---|---|---|---|
| Platforms | Windows only | Windows, Ubuntu (X11/Wayland), WSLg | New requirement |
| Notifications | `win11toast` (Windows-only, heavy winrt import) | `QSystemTrayIcon.showMessage` → `notify-send` → overlay banner. `win11toast` is an optional extra | Cross-platform, no extra RAM, no threads |
| Autostart | `.vbs` + `pythonw.exe` (spawns `wscript.exe`) | Windows: registry `Run` key. Ubuntu: XDG autostart `.desktop`. Called in-process, not via a subprocess | One less process, cross-platform |
| DB location | `goals.db` in project root | Per-user data dir (override with `MGT_DATA_DIR`) | Read-only installs, WSL `/mnt/c` slowness, WAL safety |
| Timestamps | Mixed UTC `CURRENT_TIMESTAMP` + local strings | Integer epoch seconds (UTC), converted to local only for display | Eliminates timezone/DST bugs |
| Reminders | 60 s polling timer; schedule computed once at startup (stops working after day 1) | Sleep-until-due timer, capped at 15 min, rebuilt daily | ~0 wakeups, survives suspend/resume |
| Pomodoro | Decrementing counter | Deadline-based on `time.monotonic()`, drift-free 1 wake/s only while running | Accurate after lag/suspend, 0 wakeups when paused |
| Overlay sizing | Reparent/hide widgets, then `layout().sizeHint()` | Fixed pool of single-line elided labels, arithmetic height | Removes the collapse/elongate bug class by design |
| Overlay position | Not persisted | Persisted, clamped to visible screens on show / dock / undock | Laptop docking and monitor changes |
| Main window | Always alive | Created on demand, destroyed on close | Frees widget memory while in tray |
| Horizons | Static | Daily rollover `tomorrow → today` | Tasks otherwise go stale |
| Package name | `src` | `mgt` | `src` is a collision-prone top-level name |
| Fonts/sizes | Segoe UI, pt units | Font fallback list, **px** units everywhere | Segoe UI is absent on Linux; pt differs across OSes |
| Dependencies | `pyqt6>=6.11.0` | `pyqt6>=6.7` | Avoid an unresolvable pin; nothing needs 6.11 |
| Quality | Manual checks | `pytest` for Qt-free logic, offscreen smoke tests, `--doctor` diagnostics | Verifiable on every OS |

---

## 1. Project Overview & Philosophy

**Minimalist Goal Tracker (MGT)** is an ultra-minimalist desktop productivity tool: horizon-based tasks, a floating read-only sticky overlay, an integrated Pomodoro timer, and occasional motivational check-ins.

### Core Principles
1. **Pure-black aesthetics.** `#000000` background, frameless windows, dark gray cards (`#0d0d0d` / `#111111`), no decorative animations.
2. **Near-zero idle cost.** No polling when nothing is changing. Timers exist only while something is visibly updating. Realistic target: ~0% idle CPU, ≤60 MB resident memory in tray-only mode (a Python + QtWidgets process normally sits at 40–70 MB; treat these as targets to **measure**, see §16).
3. **Horizon-based organization.** `Today`, `Tomorrow`, `This Week`, `This Month`, `This Year`, `Life Goals`.
4. **Read-only overlay.** The overlay shows today's top priorities as labels only. Dragging it can never toggle a task. All task edits happen in the main window.
5. **Graceful degradation.** Every OS-specific feature (tray, always-on-top, notifications, autostart) has a defined fallback so the app is usable even when the desktop doesn't support it.
6. **Single thread, single connection.** Everything runs on the GUI thread; SQLite calls are sub-millisecond. No worker threads, no `time.sleep`, no busy loops.

### Platform Support Matrix

| Target | Tier | Overlay always-on-top | Tray | Notifications | Autostart |
|---|---|---|---|---|---|
| Windows 10/11 | 1 (reference) | Yes | Yes | Tray toast | Registry `Run` key |
| Ubuntu 22.04/24.04, X11 session | 1 | Yes | Yes (Ubuntu ships the AppIndicator extension) | Tray message / `notify-send` | XDG autostart |
| Ubuntu, Wayland session | 1, **via XWayland** (`xcb`) | Yes under `xcb`; **not possible** under native Wayland | Yes | Same | XDG autostart |
| WSL2 + WSLg | 2 (dev/test, best-effort) | Not guaranteed | Often absent | Overlay banner fallback | Not supported |

> **Recommendation:** for daily use on a Windows laptop, run MGT natively on Windows. Use WSL for development, tests, and headless checks. WSLg compositor windows do not reliably provide always-on-top, tray icons, or autostart.

---

## 2. Technology Stack & Environment

- **Language:** Python 3.12+ (`.python-version` pins `3.12` for the widest wheel support).
- **Env manager:** `uv`
- **GUI:** `PyQt6` — import **only** `QtCore`, `QtGui`, `QtWidgets`. Never import `QtMultimedia`, `QtSvg`, `QtWebEngine`, `QtNetwork`, or `QtQuick`.
- **Storage:** stdlib `sqlite3`
- **Notifications:** Qt tray messages (default). `win11toast` is an optional extra, lazy-imported, off by default.

### `pyproject.toml`
```toml
[project]
name = "mgt"
version = "0.2.0"
description = "Minimalist Goal Tracker: horizon goals, read-only desktop overlay, Pomodoro"
readme = "README.md"
requires-python = ">=3.12"
dependencies = ["pyqt6>=6.7"]

[project.optional-dependencies]
toast = ["win11toast>=0.36.3; sys_platform == 'win32'"]

[dependency-groups]
dev = ["pytest>=8"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

### Setup per platform

**Windows (PowerShell)**
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
uv sync
uv run python main.py
```

**Ubuntu / WSL (bash)**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
sudo apt update && sudo apt install -y \
  libegl1 libgl1 libfontconfig1 libdbus-1-3 libxkbcommon-x11-0 \
  libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
  libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xinerama0
uv sync
uv run python main.py
```
`libxcb-cursor0` is mandatory for the Qt `xcb` platform plugin on Qt ≥ 6.5. If it is missing, Qt aborts at startup with a message naming it; this cannot be caught in Python, so it is documented in the README and checked by `--doctor`.

> **WSL rule:** keep the repository and the data directory on the Linux filesystem (`~/...`), **never** under `/mnt/c`. The 9P mount is slow and SQLite WAL does not work reliably on it.

---

## 3. Directory Structure

```text
Minimal-Goal-Tracker/
├── .gitignore                # .venv/, __pycache__/, *.db*, .pytest_cache/
├── .python-version           # 3.12
├── AGENTS.md
├── IMPLEMENTATION.md
├── README.md
├── pyproject.toml
├── uv.lock
├── main.py                   # 3-line launcher: from mgt.app import main; main()
├── docs/
│   ├── design_spec.md
│   └── walkthrough.md        # record measured idle CPU / RAM per platform here
├── mgt/
│   ├── __init__.py           # EMPTY (no eager imports)
│   ├── __main__.py           # python -m mgt
│   ├── app.py                # lifecycle, tray, arg handling, lazy main window
│   ├── osenv.py              # OS detection, Qt env bootstrap, paths, OS tweaks (Qt-free)
│   ├── log.py                # rotating file logging + Qt message routing
│   ├── single_instance.py    # QLocalServer/QLocalSocket
│   ├── database.py           # SQLite layer (Qt-free)
│   ├── timeutil.py           # deadline presets & status math (Qt-free)
│   ├── store.py              # DB wrapper + goals_changed signal + settings cache
│   ├── pomodoro.py
│   ├── styles.py             # QSS, fonts, generated tray icon
│   ├── ui_overlay.py
│   ├── ui_main.py
│   ├── reminders.py
│   ├── notify.py             # notification backends with fallbacks
│   └── startup.py            # autostart enable/disable per OS (+ CLI)
└── tests/
    ├── test_database.py
    ├── test_timeutil.py
    ├── test_pomodoro.py
    └── test_smoke_offscreen.py
```

**Dependency rule:** `osenv`, `log`, `database`, `timeutil`, `startup` must not import Qt, so they are testable and importable instantly. Qt is imported only in the modules that need it.

---

## 4. Cross-Platform Runtime Layer (`mgt/osenv.py`, `mgt/log.py`, `mgt/single_instance.py`)

### 4.1 Detection and Qt bootstrap
```python
import os, sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")
IS_WSL = IS_LINUX and bool(os.environ.get("WSL_DISTRO_NAME"))

def configure_qt_environment() -> None:
    """Call BEFORE a QApplication exists."""
    if IS_LINUX and "QT_QPA_PLATFORM" not in os.environ:
        pref = os.environ.get("MGT_QPA", "auto")      # auto | xcb | wayland
        if pref == "auto":
            # Native Wayland forbids client-side always-on-top and window positioning.
            # XWayland (xcb) supports both, so prefer it whenever DISPLAY exists.
            pref = "xcb" if os.environ.get("DISPLAY") else "wayland"
        os.environ["QT_QPA_PLATFORM"] = pref
```
Trade-off to document in the README: under GNOME with fractional scaling, XWayland apps can look slightly soft. Users who prefer crisp text over always-on-top set `MGT_QPA=wayland`; the overlay still works but cannot stay on top or remember its position.

### 4.2 Paths
```python
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
```

### 4.3 Logging (essential for silent boot)
Under `pythonw.exe`, `sys.stdout`/`sys.stderr` are `None`, so crashes are invisible. `log.py` must:
- Attach a `RotatingFileHandler` (`data_dir()/mgt.log`, 256 KB, 1 backup, level `WARNING`; `MGT_DEBUG=1` → `DEBUG`).
- Install `sys.excepthook` to log uncaught exceptions.
- Install `qInstallMessageHandler` so Qt warnings (stylesheet parse errors, etc.) go to the log instead of a console.
- Never attach a `StreamHandler`.

### 4.4 Single instance
Autostart plus a manual launch must not produce two overlays. `single_instance.py`:
1. Try `QLocalSocket.connectToServer("mgt-<username>")`. If it connects, send `show`, exit 0.
2. Otherwise `QLocalServer.removeServer(name)` (clears stale sockets), then `listen(name)`.
3. On `newConnection`, call the app's `open_main_window()`.

### 4.5 Best-effort OS efficiency tweaks (`osenv.apply_efficiency_tweaks()`), all wrapped in `try/except`
- **Windows:** set `AppUserModelID` (`"MGT.MinimalistGoalTracker"`) via `ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID` before creating any window (correct taskbar grouping and toast attribution). Enable EcoQoS (Windows 11 efficiency mode, which keeps the process on efficiency cores):
  ```python
  import ctypes
  from ctypes import wintypes
  class _PTS(ctypes.Structure):
      _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]
  k32 = ctypes.WinDLL("kernel32", use_last_error=True)
  k32.GetCurrentProcess.restype = wintypes.HANDLE
  k32.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
  st = _PTS(1, 1, 1)   # version 1, EXECUTION_SPEED throttling on
  k32.SetProcessInformation(k32.GetCurrentProcess(), 4, ctypes.byref(st), ctypes.sizeof(st))  # 4 = ProcessPowerThrottling
  ```
  Also lower priority: `SetPriorityClass(GetCurrentProcess(), 0x4000)` (`BELOW_NORMAL`). Always declare `argtypes`/`restype` with `wintypes.HANDLE`; the default `int` conversion breaks the pseudo-handle on 64-bit Python.
- **Linux/WSL:** `os.nice(5)`.
- **`trim_memory()`** — called ~3 s after the main window is destroyed and once after startup: Windows → `SetProcessWorkingSetSize(handle, SIZE_T(-1), SIZE_T(-1))`; Linux → `ctypes.CDLL("libc.so.6").malloc_trim(0)`.

---

## 5. Database Layer (`mgt/database.py`, Qt-free)

One SQLite file `goals.db` in `data_dir()`. **One persistent connection** opened in `init_db()` and reused (never connect per call).

### Connection setup
```python
conn = sqlite3.connect(path, isolation_level=None)   # autocommit; use explicit BEGIN for multi-statement ops
conn.row_factory = sqlite3.Row
mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
conn.execute(f"PRAGMA synchronous={'NORMAL' if mode == 'wal' else 'FULL'}")
conn.execute("PRAGMA temp_store=MEMORY")
conn.execute("PRAGMA cache_size=-512")        # 512 KB page cache
conn.execute("PRAGMA busy_timeout=2000")
```
If WAL is refused (network or `/mnt/*` filesystems) the DB silently stays in rollback-journal mode with `synchronous=FULL`.

### Schema (`PRAGMA user_version = 1`)
```sql
CREATE TABLE IF NOT EXISTS goals (
    id           INTEGER PRIMARY KEY,
    title        TEXT    NOT NULL CHECK(length(title) BETWEEN 1 AND 200),
    horizon      TEXT    NOT NULL CHECK(horizon IN ('today','tomorrow','week','month','year','life')),
    deadline     INTEGER,                                   -- epoch seconds UTC; NULL = none
    is_completed INTEGER NOT NULL DEFAULT 0 CHECK(is_completed IN (0,1)),
    completed_at INTEGER,
    created_at   INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_goals_view ON goals(horizon, is_completed, deadline);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;
```
Migrations: compare `PRAGMA user_version` to the code's schema version and run ordered migration functions; v1 needs none, but the hook must exist.

### Default settings
| Key | Default | Notes |
|---|---|---|
| `pomodoro_focus_min` | `25` | 1–120 |
| `pomodoro_break_min` | `5` | 1–60 |
| `overlay_enabled` | `1` | booleans are `"1"`/`"0"` |
| `overlay_on_top` | `1` | |
| `reminders_enabled` | `1` | |
| `selected_horizon` | `today` | restored on main window creation |
| `show_completed` | `0` | |
| `overlay_x`, `overlay_y` | *(unset)* | global logical coordinates |
| `last_rollover_date` | *(unset)* | `YYYY-MM-DD` |

*(v1 stored `always_on_top = false` while §8 mandated always-on-top. v2 resolves this with `overlay_on_top`, default on.)*

### Functions
- `init_db() -> None` — connect, pragmas, schema, defaults (`INSERT OR IGNORE`), `rollover_horizons()`.
- `get_setting(key, default="") -> str`, `set_setting(key, value) -> None`
- `add_goal(title: str, horizon: str, deadline: Optional[int] = None) -> int` — trims the title, rejects empty.
- `get_goals(horizon: Optional[str] = None, show_completed: bool = False, limit: int = 100) -> List[Dict[str, Any]]`
  - Order: `is_completed, deadline IS NULL, deadline, id`. Completed rows (when shown) are limited to the 50 most recent by `completed_at`.
- `toggle_goal_completion(goal_id: int) -> bool` — sets/clears `completed_at`; returns the new state.
- `delete_goal(goal_id: int) -> bool`
- `top_goal_titles(n: int) -> List[str]` — today's uncompleted goals in priority order (overlay and reminders).
- `rollover_horizons(today: date) -> None` — if `last_rollover_date != today`, `UPDATE goals SET horizon='today' WHERE horizon='tomorrow' AND is_completed=0`, then store the date. Idempotent. Called at init and at local midnight (see §6).
- All SQL is parameterized. No string formatting of user data.

### `mgt/timeutil.py` (Qt-free, clock injectable via `now()` for tests)
- `calculate_deadline_status(deadline: Optional[int], horizon: str = "", now: Optional[int] = None) -> DeadlineStatus`
  A `NamedTuple(label, color, level)`, so it still unpacks as v1's 3-tuple:

  | Condition (Δ = deadline − now) | Label | Color | Level |
  |---|---|---|---|
  | No deadline | `""` | `""` | `none` |
  | Δ ≤ 0 | `Overdue` | `#ff5252` | `overdue` |
  | Δ < 1 h | `{max(1, Δ//60)}m left` | `#ff5252` | `critical` |
  | Δ < 24 h | `{Δ//3600}h left` | `#ffb300` | `urgent` |
  | Δ < 72 h | `{Δ//86400}d left` | `#64b5f6` | `approaching` |
  | Otherwise, or horizon is `life` | `MM-DD` (local date) | `#888888` | `normal` |

- `compute_preset(key: str, now: datetime) -> Optional[int]` for `none`, `eod_today`, `plus_1h`, `plus_2h`, `plus_4h`, `eod_tomorrow`, `eow` (Sunday 23:59; if today is Sunday, today 23:59), `eom` (last day of month, 23:59). Uses local timezone, returns epoch seconds.

---

## 6. State Hub (`mgt/store.py`)

`Store(QObject)` is the only thing UI code talks to.
- Wraps `database` mutations and emits `goals_changed` **coalesced** (dirty flag + `QTimer.singleShot(0, ...)`), so a burst of changes triggers one UI rebuild.
- Caches all settings in a dict at startup. Reads never touch SQLite; writes are write-through.
- Owns the **midnight timer**: a single-shot set to the next local midnight (+5 s), capped at 15 min per wake like §11. On fire it calls `rollover_horizons()`, emits `goals_changed`, and re-arms.

---

## 7. Pomodoro Controller (`mgt/pomodoro.py`)

A headless `PomodoroController(QObject)` shared by the main window and overlay.

- **Signals:** `tick(time_str: str, remaining_seconds: int)`, `state_changed(is_running: bool, mode: str)`, `completed(mode: str)`.
- **Properties:** `focus_duration_minutes`, `break_duration_minutes`, `is_running`, `current_mode` (`"focus"` | `"break"`), `remaining_seconds`.
- **Methods:** `start()`, `pause()`, `toggle()`, `reset()`, `update_durations(focus_min, break_min)`.

### Efficiency rules
1. **Deadline-based.** On `start()`, store `end = time.monotonic() + remaining`. Remaining time is always computed from `end`, never decremented.
2. **No timer while paused or idle.** A single `QTimer` (single-shot, `CoarseTimer`) exists only while running.
3. **Drift-free single wake per second.** After each tick with float remaining `r` and displayed seconds `s = ceil(r)`, arm the next shot for `max(1, int((r − (s − 1)) * 1000)) + 2` ms. The display changes exactly once per second with no accumulated error.
4. **Emit `tick` only when the displayed string changes.** Listeners never redundantly repaint.
5. On reaching 0: switch mode, stop, reset `remaining` to the new mode's duration, emit `completed(finished_mode)`. Do **not** auto-start the next phase.
6. `update_durations()` while idle at full duration also resets `remaining`; while running it only affects the next phase.

---

## 8. Styling & Theme (`mgt/styles.py`)

One stylesheet applied **once** on `QApplication`. Never call `setStyleSheet` per row. Every re-polish of a styled widget is costly.

### Rules
- **Units:** use `px` for all sizes (never `pt`). `pt` maps to different pixel sizes across Windows/Linux/WSL and triggers `QFont::setPointSize` warnings when `<= 0`.
- **Fonts:** set with `QFont.setFamilies(["Segoe UI", "Ubuntu", "Noto Sans", "DejaVu Sans"])` and `setPixelSize(13)` on the app font (QSS `font-family` is not a fallback list). Do not use `-apple-system`.
- **Do NOT use `letter-spacing`** in QSS (unsupported, produces console warnings).
- **Selectors by `objectName` and dynamic properties:**

```css
* { color: #ffffff; }
QFrame#Root { background-color: #000000; border: 1px solid #222222; border-radius: 8px; }
QFrame#Card { background-color: #0d0d0d; border: 1px solid #1a1a1a; border-radius: 6px; }
QLineEdit, QComboBox, QSpinBox, QDateTimeEdit {
    background: #111111; border: 1px solid #262626; color: #ffffff;
    border-radius: 4px; padding: 6px 10px;
}
QPushButton { background: transparent; border: none; padding: 6px 10px; }
QPushButton:hover { background: #222222; }
QPushButton[nav="true"] { color: #555555; border-bottom: 2px solid transparent; }
QPushButton[nav="true"][active="true"] { color: #ffffff; border-bottom: 2px solid #ffffff; }
QLabel#Title { color: #dddddd; }
QLabel#Title[done="true"] { color: #555555; text-decoration: line-through; }
QLabel#Badge { border-radius: 4px; padding: 2px 6px; font-size: 11px; color: #888888; }
QLabel#Badge[urgency="overdue"]     { color: #ff5252; }
QLabel#Badge[urgency="critical"]    { color: #ff5252; }
QLabel#Badge[urgency="urgent"]      { color: #ffb300; }
QLabel#Badge[urgency="approaching"] { color: #64b5f6; }
QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #222222; background: #000000; }
QCheckBox::indicator:checked { background: #ffffff; }
```
Badge urgency colors come from pre-defined property selectors. Changing a badge means setting `urgency` and re-polishing **only if the level actually changed** (`style().unpolish(w); style().polish(w)`).

- **Rounded top-level windows:** top-level windows use `WA_TranslucentBackground` with margin-0 layout containing `QFrame#Root` (the rounded, bordered surface). Support `MGT_OPAQUE=1` to skip translucency (radius is then ignored) for compositors that misbehave.
- **Icon:** generate the tray/window icon at runtime with `QPainter` on a `QPixmap` (white ring on black). No asset files. Cache one `QIcon` instance.

---

## 9. Read-Only Floating Overlay (`mgt/ui_overlay.py`)

### 9.1 Window setup
```python
flags = (Qt.WindowType.FramelessWindowHint
         | Qt.WindowType.Tool                         # no taskbar / Alt+Tab entry
         | Qt.WindowType.WindowDoesNotAcceptFocus)    # never steals keyboard focus
if store.setting("overlay_on_top") == "1":
    flags |= Qt.WindowType.WindowStaysOnTopHint
self.setWindowFlags(flags)
self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
```
Changing flags on a live window hides it; the settings handler must call `show()` afterwards. Verify in the smoke tests that dragging still works with `WindowDoesNotAcceptFocus` on every platform. If it breaks on one, drop that flag there only.

### 9.2 Content (read-only)
- Rows are plain `QLabel`s: `• Task title`. **No checkboxes. No click handlers on rows.**
- Shows `top_goal_titles(MAX_ROWS)` with `MAX_ROWS = 6`; if more exist, a final muted row `+N more`. Empty state: one row, `Nothing planned for today`.
- **Mini-bar:** `MM:SS` + mode (`Focus`/`Break`) + one play/pause button wired to `controller.toggle()`. Updates only run while `isVisible()`.
- **Banner row (fallback notifications, §11):** a hidden label that `notify.py` may show for 20 s.
- **Interactions:** double-click → open/raise main window. Right-click → context menu: `Open Main Window`, `Start/Pause Timer`, `Hide Overlay`, `Quit`. (This menu keeps the app controllable when no system tray exists, e.g. WSLg or vanilla GNOME. It never modifies tasks.)
- Rebuild only on `store.goals_changed`, and only when the rendered content signature changed.

### 9.3 Deterministic geometry (replaces v1's `_sync_geometry` reparenting algorithm)
v1's collapse/infinite-growth bug comes from word-wrapped labels (whose height depends on width) feeding `layout().sizeHint()` across DPI changes. v2 removes the feedback loop:

1. Width is a constant (`WIDTH = 300` logical px).
2. A **fixed pool** of `MAX_ROWS` labels is created once. Never create or destroy labels at runtime; only `setText`/`setVisible`.
3. Labels are **single-line and elided**: `fm.elidedText("• " + title, ElideRight, WIDTH - 2*PAD)`; the tooltip holds the full title. No `wordWrap`.
4. Height is **arithmetic**, not layout-derived:
   ```python
   h = 2*PAD + HEADER_H + max(n_rows, 1) * ROW_H + (ROW_H if hidden_count else 0)
   if (WIDTH, h) != (self.width(), self.height()):
       self.setFixedSize(WIDTH, h)
   ```
   (`ROW_H`, `HEADER_H`, `PAD` are logical-px constants derived from the 13 px font; Qt scales them per screen.)
5. A `_resyncing` re-entrancy guard and signature check make `_apply_content()` idempotent.
6. **Screen changes:** `windowHandle()` is `None` before the first show, so connect `windowHandle().screenChanged` **once, inside `showEvent`** (guarded by a flag). On trigger, re-run `_apply_content(force=True)` (re-elides with the new screen's font metrics) and `_clamp_to_screens()`. Also connect `QGuiApplication.screenAdded`, `screenRemoved`, and `primaryScreenChanged` to `_clamp_to_screens()` (laptop dock/undock).

### 9.4 Position persistence and clamping
- On `moveEvent`, start a 500 ms single-shot (debounce) that writes `overlay_x/overlay_y`. (`startSystemMove` may swallow the release event, so do not rely on it.)
- On startup and screen changes: if the saved rect does not intersect at least 40×40 px of any `screen.availableGeometry()`, move to the top-right of the primary screen's available area (20 px margin).
- **Native Wayland** ignores client-side `move()`: when `QGuiApplication.platformName() == "wayland"`, skip restore/clamp/persist entirely.

### 9.5 Native movement
```python
def mousePressEvent(self, event):
    if event.button() == Qt.MouseButton.LeftButton:
        wh = self.windowHandle()
        if wh and hasattr(wh, "startSystemMove") and wh.startSystemMove():
            event.accept(); return
        self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()  # manual fallback
    super().mousePressEvent(event)

def mouseMoveEvent(self, event):
    if getattr(self, "_drag_origin", None) is not None and event.buttons() & Qt.MouseButton.LeftButton:
        self.move(event.globalPosition().toPoint() - self._drag_origin)

def mouseReleaseEvent(self, event):
    self._drag_origin = None
```
`startSystemMove()` is supported on Windows, X11, and Wayland. The manual fallback covers compositors that return `False`.

---

## 10. Main Window (`mgt/ui_main.py`)

### 10.1 Lifecycle
- Created **on demand** by `app.open_main_window()` and destroyed on close (`WA_DeleteOnClose`). This frees its widgets while the app sits in the tray. State that must survive (`selected_horizon`, `show_completed`) lives in settings.
- If neither a tray nor the overlay is available/enabled, closing the main window quits the app (otherwise it would be unreachable).
- After close: `QTimer.singleShot(3000, osenv.trim_memory)`.

### 10.2 Screen-aware sizing and placement
- Default logical size `460×680`, **clamped** to `0.9 × availableGeometry` of the target screen (a 1366×768 laptop at 150% scaling has only ≈910×512 logical px).
- Open on the screen under the cursor (`QGuiApplication.screenAt(QCursor.pos())`, falling back to primary), centered. Skipped on native Wayland.
- Frameless; custom titlebar uses `startSystemMove()` (with the same manual fallback as §9.5). The `X` button closes (→ tray).

### 10.3 Tabs
`Goals` (default) and `Settings`, in a `QStackedWidget`. The Settings page is built lazily the first time it is opened.

### 10.4 Goals view
- **Horizon bar:** pill buttons for the six horizons (the active one has `active="true"`).
- **Quick input:** `QLineEdit` (max 200 chars) + preset `QComboBox` + `Add` (Enter also adds). Presets map to `timeutil.compute_preset` keys: `No Deadline`, `End of Today (23:59)`, `In 1 Hour (+1h)`, `In 2 Hours (+2h)`, `In 4 Hours (+4h)`, `End of Tomorrow`, `End of This Week (Sun 23:59)`, `End of This Month`, `Custom Date/Time...` (lazy `QDialog` with `QDateTimeEdit`, calendar popup, default = now + 1 h).
- **Show Completed** checkbox (persisted).
- **Task list:** scroll area of row widgets (checkbox, title, urgency badge, `✕` delete).
  - Rebuild only when the ordered list of `(id, is_completed, title, deadline)` differs from what is rendered (compare a signature; never rebuild blindly).
  - Cap: 100 rows per view (+50 completed). If real usage exceeds ~200 rows, migrate to `QListView` + delegate; not needed for v1.
  - Checkbox/delete → `store` mutation → `goals_changed` → overlay and list update.
- **Live badge refresh without reloading:**
  - Keep a list of only the **dynamic rows**: those with a deadline in the future and within 72 h. Overdue and far-future rows are static and need no refresh.
  - A single-shot timer is aligned to the next minute boundary (+1 s), runs **only while the window is visible**, and on `showEvent` does one immediate refresh.
  - Each refresh updates text and the `urgency` property only when they changed. No list reload, no scroll or focus loss.
- **Pomodoro bar:** `MM:SS`, mode badge, play/pause, reset. Connects to the shared controller on creation; destruction disconnects automatically.

### 10.5 Settings view
Focus minutes (`QSpinBox` 1–120), break minutes (1–60), `Show overlay`, `Overlay always on top` (with a hint label under native Wayland: *"Not supported by this display server"*), `Random reminders`, `Start on login` (calls `startup.enable()/disable()` **in-process**; disabled with an explanatory hint under WSL), and `Save Settings` (writes SQLite via `store`, calls `controller.update_durations`, applies overlay flags).

---

## 11. Reminders & Notifications (`mgt/reminders.py`, `mgt/notify.py`)

### 11.1 Scheduling (zero polling)
1. `WINDOWS = ((10, 12), (14, 16), (19, 21))`. Pick one random minute within each window per day → 3 due times, sorted.
2. Keep a single `QTimer` (single-shot, `VeryCoarseTimer`). On each arm compute `wait = min(next_due − now, 15 min)` (minimum 1 s) using the **wall clock**. The 15-minute cap makes it robust to suspend/resume and clock changes while costing at most one wake per 15 minutes.
3. On wake: if `now ≥ next_due`, fire only if `now − next_due ≤ 10 min` (a reminder missed while suspended is skipped, not delivered late); then advance. When no due times remain today, build tomorrow's schedule (this fixes v1's "works only on the first day" flaw).
4. Skip and defer by 10 minutes if a **Focus** Pomodoro session is currently running.

### 11.2 Content
- Title: `Minimalist Goal Tracker`.
- Body: `Focus Check: Have you worked on '{top_task}' today?`, using `top_goal_titles(1)`. If there are no tasks, use a random entry from a built-in tuple of ~15 short stoic/minimalist quotes (no network).
- No sound, no focus stealing.

### 11.3 Backends (`notify.py`, first available wins, resolved once at startup)
1. **Tray message:** `QSystemTrayIcon.showMessage(title, body, NoIcon, 8000)` when the tray is visible and `supportsMessages()`. Native toast on Windows, D-Bus on Ubuntu. No extra dependency.
2. **Linux `notify-send`:** `shutil.which` checked once; sent with `QProcess.startDetached("notify-send", ["--app-name=MGT", "--", title, body])`. This is non-blocking, uses no shell, and leaves no zombie.
3. **Overlay banner:** display the text in the overlay's banner row for 20 s. This always works, even under WSLg.
4. **Optional** `win11toast` (only when the `toast` extra is installed and `MGT_TOAST=1`): lazy import on first use. It pulls in heavy winrt modules, so it is not the default.

---

## 12. Autostart (`mgt/startup.py`, Qt-free)

API: `is_supported() -> bool`, `is_enabled() -> bool`, `enable() -> None`, `disable() -> None`, plus a CLI: `python -m mgt.startup --enable | --disable | --status`. Absolute paths come from `Path(__file__).resolve()` and `sys.executable`, never from the current directory.

| OS | Mechanism |
|---|---|
| **Windows** | `winreg` value `MGT` under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` with data `"<pythonw.exe>" "<main.py>" --minimized`. `pythonw.exe` = `Path(sys.executable).with_name("pythonw.exe")` (fallback: `sys.executable`). No `.vbs`, no `wscript.exe`: zero console flashes and one fewer process. |
| **Ubuntu** | `~/.config/autostart/mgt.desktop` with `Type=Application`, `Name=MGT`, `Exec=env MALLOC_ARENA_MAX=2 <venv python> <main.py> --minimized`, `X-GNOME-Autostart-Delay=5` (avoids login contention), `NoDisplay=true`. |
| **WSL** | `is_supported()` returns `False` (no real login session). The Settings toggle is disabled with a hint. |

*(`register_startup.py` is removed. A subprocess to flip a toggle would spawn a whole extra Python interpreter.)*

---

## 13. Entry Point & Tray (`main.py`, `mgt/app.py`)

`main.py` is only: `from mgt.app import main; main()`.

Startup sequence in `app.main()` (order matters):
1. Parse `sys.argv` manually (no `argparse`): `--minimized`, `--doctor`, `--version`.
2. `osenv.configure_qt_environment()`, then `log.setup()`.
3. Import Qt. `QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)` (correct 125%/150% scaling; Qt 6 needs no other high-DPI attributes). Create `QApplication`; `setQuitOnLastWindowClosed(False)`.
4. `osenv.apply_efficiency_tweaks()` (AUMID must precede any window).
5. Single-instance check (§4.4).
6. `init_db()`; build `Store`, shared `PomodoroController`.
7. Apply font and stylesheet (§8).
8. If `overlay_enabled`: create `OverlayWidget`. Restore its position and show it.
9. Create the tray (§13.1). Start `ReminderScheduler` and the midnight timer.
10. Unless `--minimized`, open the main window.
11. `gc.collect(); gc.freeze(); osenv.trim_memory()`; `sys.exit(app.exec())`.

### 13.1 Tray
- `QSystemTrayIcon.isSystemTrayAvailable()` decides everything. If `False`, skip the tray and rely on the overlay context menu (§9.2).
- Menu: `Open Main Window`, `Toggle Overlay`, `Exit`. The menu is the primary, reliable interaction on every platform.
- Left-click (`activated` with `Trigger`) toggles the main window. Some Linux AppIndicator hosts do not emit this, so never rely on it.

### 13.2 `--doctor`
Prints and exits (no windows): OS, WSL flag, session type, chosen `QT_QPA_PLATFORM`, Qt/PyQt versions, every screen (name, geometry, available geometry, devicePixelRatio, logical DPI), tray availability, notification backend selected, data dir, DB journal mode, whether `libxcb-cursor0` and `notify-send` are present, and a warning if the working directory is under `/mnt/`. Support questions about "it doesn't show up on my laptop" start here.

---

## 14. Efficiency Rules (Checklist for Every Module)

1. **No idle timers.** Allowed timers: Pomodoro (only while running), badge refresh (only while the main window is visible), reminders (≤ 1 wake / 15 min), midnight (≤ 1 wake / 15 min), a 500 ms debounce for position saves.
2. Prefer `Qt.TimerType.CoarseTimer`/`VeryCoarseTimer` so the OS can coalesce wakeups.
3. **Update only on change:** compare to the previous value before `setText`, `setProperty`, `setFixedSize`, or re-polish.
4. **No full-list rebuilds** for time-driven updates. Refresh dynamic rows in place.
5. **Lazy everything:** main window, Settings page, date dialog, optional toast library.
6. **Import discipline:** only `QtCore`, `QtGui`, `QtWidgets`; empty `__init__.py`; Qt-free core modules. Check with `python -X importtime main.py`.
7. **No threads, no `time.sleep`, no subprocess in hot paths**, no `shell=True`.
8. **No OpenGL, no `QGraphicsEffect` (drop shadows/blur), no per-pixel animations.**
9. Settings are cached in memory; SQLite is touched only on mutations and at startup.
10. Free memory when idle: destroy the main window on close, `gc.freeze()` after startup, `trim_memory()` after hide.

---

## 15. Implementation Phases for Agents

| Phase | Work | Done when |
|---|---|---|
| 0 | `pyproject.toml`, `osenv.py`, `log.py`, `.gitignore`, `.python-version`, `python -m mgt --doctor` skeleton | `--doctor` runs on Windows and Ubuntu |
| 1 | `database.py`, `timeutil.py` + `tests/test_database.py`, `tests/test_timeutil.py` | `uv run pytest` green with no Qt installed |
| 2 | `store.py`, `pomodoro.py` + `tests/test_pomodoro.py` (fake clock) | Tick/complete/pause logic verified headless |
| 3 | `styles.py` (QSS, fonts, generated icon) | No Qt warnings in `mgt.log` |
| 4 | `notify.py`, `reminders.py` | Backend resolves on each OS; schedule logic unit-tested with a fake clock |
| 5 | `ui_overlay.py` | Add/remove 20 tasks, drag, no geometry drift; right-click menu works |
| 6 | `ui_main.py` | Presets, badge refresh, lazy settings, on-demand lifecycle |
| 7 | `startup.py`, `single_instance.py` | Enable/disable verified per OS; second launch focuses the first |
| 8 | `app.py`, `main.py`, tray wiring | `--minimized` boots to overlay/tray with no main window created |
| 9 | Full verification matrix (§16) and docs | Measurements recorded in `docs/walkthrough.md` |

---

## 16. Verification & Validation Protocol

### 16.1 Automated (run on Windows, Ubuntu, and WSL)
```bash
# 1. Imports
uv run python -c "import mgt.app, mgt.osenv, mgt.log, mgt.database, mgt.timeutil, mgt.store, mgt.pomodoro, mgt.styles, mgt.ui_main, mgt.ui_overlay, mgt.reminders, mgt.notify, mgt.startup; print('ALL MODULES OK')"

# 2. Logic tests (Qt-free parts need no display)
uv run pytest

# 3. Headless GUI instantiation (no display server required)
QT_QPA_PLATFORM=offscreen uv run pytest tests/test_smoke_offscreen.py     # Windows PowerShell: $env:QT_QPA_PLATFORM="offscreen"

# 4. Diagnostics
uv run python -m mgt --doctor
```
Database smoke test (use a temp dir so you never touch real data):
```bash
MGT_DATA_DIR=$(mktemp -d) uv run python -c "from mgt.database import init_db, add_goal, get_goals; init_db(); add_goal('Test','today'); print(get_goals('today'))"
```
`test_smoke_offscreen.py` must construct the overlay and main window with 0, 1, 6, and 50 tasks, with the stylesheet applied, and assert: no exceptions, no stylesheet warnings (capture via the Qt message handler), overlay height is identical across repeated `goals_changed` emissions, and the overlay never exceeds `MAX_ROWS + 1` visible rows.

### 16.2 Manual platform matrix
| Scenario | Win 10/11 | Ubuntu X11 | Ubuntu Wayland (xcb) | WSLg |
|---|---|---|---|---|
| Overlay stays on top, doesn't appear in Alt+Tab/taskbar | ✔ | ✔ | ✔ | best-effort |
| Drag overlay between 4K@150% and 1080p@100%: no size change, text re-elides | ✔ | ✔ | ✔ | n/a |
| Dock/undock a monitor: overlay lands on a visible screen | ✔ | ✔ | ✔ | n/a |
| Main window fits a 1366×768 / 150% laptop screen | ✔ | ✔ | ✔ | ✔ |
| Tray menu works; with no tray, overlay right-click menu works | ✔ | ✔ | ✔ | ✔ |
| Reminder appears (tray toast / `notify-send` / banner) | ✔ | ✔ | ✔ | banner |
| Reboot with autostart: no console flash, overlay appears, one instance only | ✔ | ✔ | ✔ | n/a |
| Suspend/resume: Pomodoro correct, no late reminder burst | ✔ | ✔ | ✔ | n/a |
| Local-midnight rollover moves `tomorrow` → `today` (inject clock) | ✔ | ✔ | ✔ | ✔ |

### 16.3 Resource measurement (record results in `docs/walkthrough.md`)
Measure after 5 minutes idle in tray-only mode (`--minimized`), then with the main window open, then with Pomodoro running.
- **Windows:** Task Manager → Details → *CPU* and *Memory (private working set)*; `typeperf` or Resource Monitor for wakeups.
- **Ubuntu/WSL:** `ps -o rss,pcpu,nlwp -p $(pgrep -f "main.py")`, `pidstat -u -w -p <pid> 5 12` (CPU and context switches), `strace -c -f -p <pid>` for a 60 s syscall count.
- **Targets (verify, don't assume):** idle CPU ≈ 0.0–0.1%; wakeups ≈ 0 when idle and ≤ 1/s when the Pomodoro is running; tray-only resident memory ≤ 60 MB; memory returns close to baseline after closing the main window.

---

## 17. Platform Gotchas & Fixes (Quick Reference)

| Symptom | Cause | Fix |
|---|---|---|
| `Could not load the Qt platform plugin "xcb"` | Missing `libxcb-cursor0` (Qt ≥ 6.5) | `sudo apt install libxcb-cursor0` and the rest of §2's list |
| `libEGL.so.1` / `libGL.so.1` not found | Minimal Ubuntu/WSL image | `sudo apt install libegl1 libgl1` |
| Overlay won't stay on top on Ubuntu | Native Wayland forbids it | Default `xcb` (XWayland); don't set `MGT_QPA=wayland` |
| Overlay position not remembered / ignored | Wayland compositors ignore `move()` | Expected; skip persistence on `wayland` |
| Text looks soft on Ubuntu | XWayland + fractional scaling | `MGT_QPA=wayland` (loses always-on-top) |
| No tray icon on WSLg / vanilla GNOME | No StatusNotifier host | Use overlay right-click menu |
| App silent-crashes on boot | `pythonw` has no console | Read `data_dir()/mgt.log` |
| Overlay height collapses/grows | Word-wrap + `sizeHint` loop | Use §9.3 (single-line elided pool, arithmetic height) |
| `windowHandle()` is `None` | Window not yet shown | Connect `screenChanged` in `showEvent` |
| QSS console warnings | `letter-spacing`, `pt <= 0` | Not used; `px` units only |
| Slow or locked SQLite on WSL | DB on `/mnt/c` | Keep data on the Linux filesystem; WAL auto-falls back |
| Two overlays after login | Autostart + manual launch | Single-instance guard (§4.4) |
