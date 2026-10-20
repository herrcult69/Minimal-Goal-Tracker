# Minimalist Goal Tracker (MGT) — System Rules & Context

You are working on the **Minimalist Goal Tracker (MGT)** repository.

## ⚡ Agent Cost & Token Optimization Directive (STRICT)
- **Terse Output / Zero Fluff:** Do NOT output conversational chatter, explanations of standard libraries, or restatements of requirements. Output only exact diffs, concise rationale, and verification results.
- **Do NOT Read Bloated Files:** NEVER read `uv.lock`, `.venv/**`, `goals.db` (binary), or media files. All dependencies are declared in `pyproject.toml`.
- **Surgical Edits Only:** Make minimal, targeted search/replace edits. Never regenerate an entire 500+ line file when changing a few lines.
- **NEVER Run GUI Interactively:** Running `uv run python main.py` starts a blocking GUI loop that hangs the terminal and wastes timeout tokens. Use one-line headless smoke tests instead.

## Project Vision & Philosophy
1. **Ultra-Minimalist & Dark Theme:** Pure-black aesthetics (`#000000`), borderless/frameless layout, minimal distractions, no heavy animations or bloated dependencies.
2. **Near-Zero Resource Footprint:** Designed to run 24/7 in the background with ~0% CPU at idle and minimal RAM footprint (<50MB).
3. **Horizon-Based Goal Organization:** Goals belong to time horizons: `Today`, `Tomorrow`, `This Week`, `This Month`, `This Year`, and `Life Goals`.
4. **Read-Only Desktop Sticky Overlay:** A separate floating always-on-top window that displays today's top priorities. It is explicitly kept **read-only** so users can drag it across the screen without accidentally checking off tasks or modifying state. All interactions occur in the main app window.
5. **Integrated Focus & Reminders:**
   - A toggleable Pomodoro timer at the bottom of the main window.
   - Background random motivational and goal check-in reminders using native Windows 11 toast notifications (`win11toast`).
6. **Windows Startup Automation:** Runs on Windows startup via `pythonw.exe` from `.venv` so that no console window flashes on boot.

## Architecture & Codebase Map
- `pyproject.toml` / `uv.lock`: Dependency and environment management powered by `uv`.
- `main.py`: Entry point, system tray icon, window coordination, and `--minimized` boot support.
- `register_startup.py`: Script to enable or disable automatic launch on Windows boot via `shell:startup`.
- `src/database.py`: SQLite3 database layer with schema creation and deadline urgency calculations.
- `src/styles.py`: Pure-black QSS stylesheet.
- `src/ui_main.py`: Interactive management window with horizon tabs, task input, deadline formatting, and pomodoro toggle.
- `src/ui_overlay.py`: Frameless, draggable, read-only desktop overlay with multi-monitor DPI sync.
- `src/pomodoro.py`: 25/5-minute focus timer using PyQt6 `QTimer`.
- `src/reminders.py`: Background random reminder scheduler with Windows toast delivery.
- `IMPLEMENTATION.md`: Full architectural specification for agents.
- `CLAUDE.md`: Token and cost optimization directives.
- `docs/`: Design specifications and walkthrough documentation.

## Development & Execution Protocol
- Always use `uv run python <script>` to run commands within the isolated virtual environment.
- Headless verification:
  ```powershell
  uv run python -c "import main, register_startup, src.database, src.pomodoro, src.styles, src.ui_main, src.ui_overlay, src.reminders; print('OK')"
  ```
