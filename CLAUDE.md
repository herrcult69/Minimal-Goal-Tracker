# CLAUDE.md — Minimalist Goal Tracker (MGT)
> **Cost & Token Optimization Directive for Autonomous Coding Agents (Claude Code, Aider, etc.)**

## ⚡ 1. Token & Cost Reduction Rules (STRICT)
- **Terse Output / Zero Fluff:** Do NOT output conversational chit-chat, explanations of standard Python libraries, or restatements of requirements. Output only exact diffs, concise rationale, and verification results.
- **Do NOT Read Bloated Files:** NEVER read `uv.lock`, `.venv/**`, `goals.db` (binary), or media files. All required dependencies are already declared in `pyproject.toml`.
- **Surgical Edits Only:** Make minimal, targeted search/replace edits. Never regenerate an entire 500+ line file when changing a few lines.
- **No Broad File Searching:** Do NOT run broad `find` or `grep` sweeps across the repository. Use the Codebase Map below.
- **NEVER Run GUI Interactively:** Running `uv run python main.py` starts a blocking GUI loop that will hang the terminal and exhaust timeout tokens. Run ONLY headless one-line verification commands.

---

## 🧭 2. Codebase Map (Do Not Search Blindly)
| File | Responsibility |
| :--- | :--- |
| `main.py` | App lifecycle, shared `PomodoroController`, system tray icon, `--minimized` boot. |
| `src/database.py` | SQLite layer (`goals` & `settings` tables), CRUD, deadline urgency calculator. |
| `src/pomodoro.py` | Headless `PomodoroController(QObject)` emitting signals (`tick`, `state_changed`, `completed`). |
| `src/styles.py` | Pure-black QSS stylesheet (`#000000`, Segoe UI, pill badges, custom inputs). |
| `src/ui_main.py` | Main interactive window: horizon tabs, deadline presets dropdown, 60s live badge refresh, settings tab. |
| `src/ui_overlay.py` | Read-only floating sticky overlay: live clock, today priorities, mini pomodoro, multi-monitor DPI sync. |
| `src/reminders.py` | Random check-in toasts via native `win11toast`. |
| `register_startup.py`| Installs silent `.vbs` launcher using `pythonw.exe` in `shell:startup`. |
| `IMPLEMENTATION.md` | Full architecture blueprint (v2). |
| `IMPLEMENTATION_V3.md` | New feature spec: Overlay deadline badges, horizon rules & rollovers. |

---

## 🛠️ 3. Execution & Verification Commands
Always use `uv run python <command>` within the isolated environment:
- **Fast Syntax & Import Check (Minimal Tokens):**
  ```powershell
  uv run python -c "import main, register_startup, src.database, src.pomodoro, src.styles, src.ui_main, src.ui_overlay, src.reminders; print('OK')"
  ```
- **Database CRUD Smoke Test:**
  ```powershell
  uv run python -c "from src.database import init_db, add_goal, get_goals; init_db(); print('DB OK')"
  ```
- **Headless UI Instantiation (Exits cleanly without blocking terminal):**
  ```powershell
  uv run python -c "import sys; from PyQt6.QtWidgets import QApplication; app = QApplication(sys.argv); from src.pomodoro import PomodoroController; p = PomodoroController(); from src.ui_main import MainWindow; from src.ui_overlay import OverlayWidget; w1 = MainWindow(p); w2 = OverlayWidget(p); print('UI OK'); sys.exit(0)"
  ```

---

## ⚠️ 4. Critical Architecture Gotchas (Avoid Costly Loops)
1. **Frameless Dragging:** Use `self.windowHandle().startSystemMove()` in `mousePressEvent`. Never implement manual mouse delta math (causes stutter and DPI scaling crashes).
2. **Overlay Geometry Stability:**
   - Width is strictly fixed at `300px`.
   - Never add checkboxes to overlay (must remain **read-only** so dragging never modifies task state).
   - In `_sync_geometry()`: detach old widgets (`widget.setParent(None)`), call `layout().activate()`, then `setFixedSize(300, layout().sizeHint().height())`. Hook `screenChanged` on window handle for multi-monitor DPI switches.
3. **QSS Warnings:**
   - Qt does NOT support `letter-spacing` in QSS. Never add `letter-spacing`.
   - Never set font point size `<= 0`.
4. **Live Deadline Updates:**
   - Update existing badge labels in-place via a 60-second timer. Do NOT wipe and rebuild the task layout (avoids scroll jumps).
5. **Windows Startup:**
   - Must use `.venv/Scripts/pythonw.exe` with `WshShell.Run ..., 0, False` to prevent CMD console window popups.
