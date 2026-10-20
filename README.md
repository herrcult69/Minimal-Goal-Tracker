# Minimalist Goal Tracker (MGT)

A pure-black, always-on-top desktop goal tracker: horizon-based goals
(today / week / month / later), deadline urgency badges, a read-only floating
sticky overlay with a live clock, an integrated Pomodoro timer, and occasional
check-in notifications. Near-zero idle cost; lives in the system tray.

Primary target is **Windows 10/11**; the same code also runs on Linux/X11
(Wayland via XWayland) — see [Platform notes](#platform-notes).

## Setup & Installation

### 1. Prerequisites
- **OS**: Windows 10/11 (or Linux/X11)
- **uv**: Modern, extremely fast Python package manager. If not yet installed:
  ```powershell
  # Windows (PowerShell):
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  # or via WinGet:
  winget install astral-sh.uv
  ```
  *(Python >= 3.12 is fetched and managed automatically by `uv`).*

### 2. Clone & Sync Dependencies
```bash
git clone git@github.com:herrcult69/Minimal-Goal-Tracker.git
cd Minimal-Goal-Tracker

# Create the virtual environment and install all dependencies:
uv sync

# (Optional) Install Windows toast notification support:
uv sync --extra toast
```

## Running the App

- **Standard Launch** (interactive main window + floating desktop overlay + system tray):
  ```bash
  uv run python main.py
  ```
- **Silent Boot / Minimized** (starts hidden into system tray and floating overlay only):
  ```bash
  uv run python main.py --minimized
  ```
- **Detached Launch (No Terminal)**:
  Double-click `launch.vbs` or run:
  ```powershell
  wscript.exe //B launch.vbs
  ```
- **Headless Diagnostics** (checks environment, DPI, tray, and display without opening any windows):
  ```bash
  uv run python main.py --doctor
  uv run python main.py --version
  ```

## Global Command Shortcut (`mgt`)

To start or control MGT from any command prompt or PowerShell window without navigating to the repository folder:

1. Copy or create a launcher script named `mgt.cmd` inside any folder on your system `PATH` (such as `%LOCALAPPDATA%\Microsoft\WinGet\Links\` or `%USERPROFILE%\.local\bin\`):
   ```bat
   @echo off
   set "SCRIPT_DIR=P:\path\to\Minimal-Goal-Tracker"

   if "%~1"=="--doctor" goto console
   if "%~1"=="--version" goto console
   if "%~1"=="-v" goto console
   if "%~1"=="--help" goto console
   if "%~1"=="-h" goto console

   wscript.exe //B "%SCRIPT_DIR%\launch.vbs" %*
   goto :eof

   :console
   pushd "%SCRIPT_DIR%"
   "%SCRIPT_DIR%\.venv\Scripts\python.exe" "%SCRIPT_DIR%\main.py" %*
   popd
   ```
2. You can then simply type:
   ```powershell
   mgt              # Launches MGT silently in the background
   mgt --minimized  # Starts to tray/overlay only
   mgt --doctor     # Runs diagnostics directly in terminal
   ```

## Autostart

```bash
uv run python -m mgt.startup --enable    # silent login autostart
uv run python -m mgt.startup --status
uv run python -m mgt.startup --disable
```

On Windows this writes a hidden `.vbs` launcher (using `pythonw.exe`, so no
console flashes) into `shell:startup` plus a `HKCU ...\Run` value; on Linux it
writes an XDG `.desktop` entry. The same toggle is available in
**Settings → System** inside the app.

## Notifications

Reminders resolve a backend once at startup and fall through until one works, so
a missing dependency never kills a check-in:

1. `QSystemTrayIcon.showMessage` (native toast / D-Bus)
2. `notify-send` (detached `QProcess`, no shell)
3. the overlay's banner row — always available, even under WSLg
4. optional lazy `win11toast`, if the extra is installed **and** `MGT_TOAST=1`

```bash
uv sync --extra toast && MGT_TOAST=1 uv run python main.py
```

## Tests

```bash
uv run pytest                                              # headless unit tests
QT_QPA_PLATFORM=offscreen uv run pytest tests/test_smoke_offscreen.py   # widget smoke test
```

The suite needs no display, no network, and never launches the real GUI event
loop; it runs against a temporary data directory.

## Layout

| File | Responsibility |
| :--- | :--- |
| `main.py` | Thin launcher → `mgt.app.main()` |
| `mgt/app.py` | Lifecycle, argv handling, tray, surfaces, `--doctor` |
| `mgt/store.py` | State hub: the only object the UI talks to |
| `mgt/database.py` | SQLite schema, CRUD, settings, horizon rollover, urgency (Qt-free) |
| `mgt/timeutil.py` | Deadline presets and status-bar math; injectable clock (Qt-free) |
| `mgt/pomodoro.py` | Headless Pomodoro controller (`tick`, `state_changed`, `completed`) |
| `mgt/ui_main.py` | Main window: horizon tabs, deadline presets, 60 s live badges, Settings |
| `mgt/ui_overlay.py` | Read-only floating overlay: live clock, today priorities, mini Pomodoro |
| `mgt/styles.py` | Pure-black QSS theme, app font, tray icon |
| `mgt/notify.py` | Notification dispatch and fallback chain |
| `mgt/reminders.py` | Zero-polling random check-in reminders |
| `mgt/startup.py` | Per-OS autostart enable/disable/status |
| `mgt/single_instance.py` | Single-instance guard (QLocalServer) |
| `mgt/osenv.py` | OS detection, Qt env bootstrap, data paths, efficiency tweaks |
| `mgt/log.py` | Rotating file logging, Qt message routing |

`Qt-free` modules are importable and testable without a display.

## Data location

| OS | Path |
| :--- | :--- |
| Windows | `%LOCALAPPDATA%\MGT\goals.db` |
| Linux | `~/.local/share/mgt/goals.db` |

Override with `MGT_DATA_DIR`. Logs go to a sibling `logs/` directory. The
database is never committed — `.gitignore` keeps `*.db` and `.venv/` out.

## Platform notes

- Frameless dragging uses `windowHandle().startSystemMove()` — no manual mouse
  math, so HiDPI scaling stays stable.
- The overlay is fixed at 300 px wide, strictly read-only, and re-syncs its
  geometry on `screenChanged` for multi-monitor DPI switches.
- On Windows the process asks for EcoQoS throttling, `BELOW_NORMAL` priority
  and returns freed pages to the OS after each Pomodoro break.
- On Wayland, always-on-top and window positioning require XWayland: the app
  picks `xcb` automatically when `DISPLAY` is set. Force with `MGT_QPA=wayland`.

## Docs

`docs/IMPLEMENTATION.md` (v2 architecture) and `docs/IMPLEMENTATION_V3.md`
(horizon rules, rollover, overlay deadlines). They predate the `src/` → `mgt/`
package rename, so treat the layout table above as the current source of truth.

## License

MIT — see [LICENSE](LICENSE).
