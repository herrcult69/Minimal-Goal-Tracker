# Implementation Specification v3: Horizon Rollover & Overlay Deadline Badges

> **Target Agent:** Claude Code (CLI)  
> **Repository:** `mgt` (`P:\AntiGravity\ClaudeTest`)  
> **Scope:** Add live deadline urgency badges to the Desktop Sticky Overlay, enforce horizon-specific deadline rules, and implement automated calendar rollovers (midnight + weekend cascades).

---

## 1. Executive Summary of Features to Implement

### Feature 1: Live Deadline Urgency Badges on Sticky Overlay (`ui_overlay.py`)
- **Problem:** Currently, overlay task rows only display a flat string: `• Task title`. Deadlines and urgency states (`Overdue`, `35m left`, `2h left`) are completely missing from the overlay.
- **Solution:** Upgrade the overlay's deterministic label pool so each row renders both the elided task title and its color-coded urgency badge (`QLabel#Badge` with dynamic `urgency` property). Badges tick down in-place via the existing clock signal without triggering layout reflows or resizing bugs.

### Feature 2: Horizon-Specific Deadline Rules & Automated Cascading Rollovers
- **Problem:** Horizons are currently static (tasks in "Tomorrow" or "This Week" never move), and the deadline dropdown allows picking any deadline regardless of the active tab.
- **Solution:**
  1. **Strict Horizon Deadline Rules on Creation:**
     - **`Today` Tab:** User can pick hourly presets (`In 1 Hour`, `In 2 Hours`, `In 4 Hours`) or default `End of Today` (`23:59:59`). Max ceiling: end of today. Default: `End of Today`.
     - **`Tomorrow` Tab:** User can pick a time tomorrow or default `End of Tomorrow` (`23:59:59`). Max ceiling: end of tomorrow. Default: `End of Tomorrow`.
     - **`This Week` Tab:** Dropdown is locked/hidden. Automatically assigned to **Sunday 23:59:59** of the current week.
     - **`This Month` Tab:** Dropdown is locked/hidden. Automatically assigned to the **last day of the current month 23:59:59**.
     - **`This Year` Tab:** Dropdown is locked/hidden. Automatically assigned to **December 31st 23:59:59**.
     - **`Life Goals` Tab:** Dropdown is locked/hidden. No deadline (`None`).
  2. **Automated Time-Based Rollovers (`database.py`):**
     - **Midnight Rollover (Every day at 00:00:00):** Uncompleted tasks in `tomorrow` move to `today`.
     - **Weekend Cascades:**
       - On **Saturday** (`weekday == 5`): Uncompleted tasks in `week` move to `tomorrow` (since Sunday is tomorrow).
       - On **Sunday** (`weekday == 6`): Uncompleted tasks in `week` move directly to `today` (since Sunday is today).
     - Triggers on startup (`init_db()`) and dynamically at local midnight via `Store._on_midnight()`.

---

## 2. File-by-File Changes & Contracts

### 2.1. `mgt/timeutil.py`

#### A. Add End of Year (`eoy`) Preset
Extend `PRESET_KEYS` and `compute_preset()`:
```python
# Add "eoy" to PRESET_KEYS
def compute_preset(key: str, now: datetime) -> Optional[int]:
    ...
    if key == "eoy":
        return _ts(now.replace(month=12, day=31, hour=23, minute=59, second=0, microsecond=0))
```

#### B. Horizon Presets & Auto-Assignment Helper
Add a helper function to return available presets and default deadline for any horizon:
```python
def get_horizon_deadline_config(horizon: str, now: Optional[datetime] = None) -> Tuple[bool, Optional[int], List[Tuple[str, str]]]:
    """Returns (allow_choice, default_epoch, preset_options).
    
    preset_options is a list of (label, preset_key).
    If allow_choice is False, the UI disables/hides custom selection and applies default_epoch.
    """
    now = datetime.now() if now is None else now
    
    if horizon == "today":
        eod = compute_preset("eod_today", now)
        options = [
            ("End of Today (23:59)", "eod_today"),
            ("In 1 Hour", "plus_1h"),
            ("In 2 Hours", "plus_2h"),
            ("In 4 Hours", "plus_4h"),
        ]
        # Filter out options that would spill past today 23:59:59
        valid_options = []
        for label, key in options:
            ts = compute_preset(key, now)
            if ts is not None and ts <= eod:
                valid_options.append((label, key))
        if not valid_options:
            valid_options = [("End of Today (23:59)", "eod_today")]
        return (True, eod, valid_options)

    elif horizon == "tomorrow":
        eod_tom = compute_preset("eod_tomorrow", now)
        return (True, eod_tom, [
            ("End of Tomorrow (23:59)", "eod_tomorrow"),
            ("Tomorrow Morning (09:00)", "tom_morning"),
            ("Tomorrow Afternoon (14:00)", "tom_afternoon"),
        ])

    elif horizon == "week":
        return (False, compute_preset("eow", now), [("Auto: End of Week (Sun 23:59)", "eow")])

    elif horizon == "month":
        return (False, compute_preset("eom", now), [("Auto: End of Month", "eom")])

    elif horizon == "year":
        return (False, compute_preset("eoy", now), [("Auto: End of Year (Dec 31)", "eoy")])

    else:  # life
        return (False, None, [("No Deadline", "none")])
```

---

### 2.2. `mgt/database.py`

#### A. Query Goals with Deadlines for the Overlay
Add `top_goals(n: int) -> List[Dict[str, Any]]`:
```python
def top_goals(n: int) -> List[Dict[str, Any]]:
    """Today's uncompleted goals with deadlines for overlay and reminders."""
    rows = _require().execute(
        "SELECT id, title, deadline FROM goals WHERE horizon = 'today' AND is_completed = 0 "
        "ORDER BY deadline IS NULL, deadline ASC, id DESC LIMIT ?",
        (n,),
    ).fetchall()
    return [dict(r) for r in rows]
```
*(Keep `top_goal_titles()` as a lightweight wrapper returning `[g["title"] for g in top_goals(n)]` so existing notification/reminder tests continue to pass).*

#### B. Implement Weekend & Daily Horizon Rollover
Update `rollover_horizons(today: date)`:
```python
def rollover_horizons(today: date) -> None:
    """Cascade incomplete goals down horizons once per day. Idempotent."""
    key = "last_rollover_date"
    stamp = today.isoformat()
    if get_setting(key, "") == stamp:
        return

    conn = _require()
    # 1. Daily: tomorrow -> today
    conn.execute(
        "UPDATE goals SET horizon = 'today' WHERE horizon = 'tomorrow' AND is_completed = 0"
    )

    # 2. Weekend cascades for 'week' goals:
    weekday = today.weekday()  # Monday=0 .. Saturday=5, Sunday=6
    if weekday == 5:
        # On Saturday: 'week' goals become 'tomorrow' (Sunday)
        conn.execute(
            "UPDATE goals SET horizon = 'tomorrow' WHERE horizon = 'week' AND is_completed = 0"
        )
    elif weekday == 6:
        # On Sunday: 'week' goals become 'today' (final day of week)
        conn.execute(
            "UPDATE goals SET horizon = 'today' WHERE horizon = 'week' AND is_completed = 0"
        )

    set_setting(key, stamp)
```

---

### 2.3. `mgt/store.py`

1. Expose `top_goals(self, n: int) -> List[Dict[str, Any]]`:
   ```python
   def top_goals(self, n: int) -> List[Dict[str, Any]]:
       return database.top_goals(n)
   ```
2. When `add_goal(title, horizon, deadline=None)` is called:
   If `deadline` is `None` and `horizon in ("today", "tomorrow", "week", "month", "year")`, resolve the default deadline from `timeutil.get_horizon_deadline_config(horizon)` before writing to SQLite.

---

### 2.4. `mgt/ui_overlay.py`

#### A. Redesign the Row Label Pool
Instead of a single `QLabel` per row, each row in `self._rows` holds two widgets:
1. `title_label = QLabel("", root)` (shows `• Title`, elided)
2. `badge_label = QLabel("", root)` (shows urgency text, styled with `objectName="Badge"`, `urgency="overdue"|"critical"|"urgent"|"approaching"|"normal"`)

```python
BADGE_WIDTH = 64  # Fixed logical px allocated for deadline badges

class OverlayRowWidgets:
    def __init__(self, title_lbl: QLabel, badge_lbl: QLabel):
        self.title_lbl = title_lbl
        self.badge_lbl = badge_lbl

    def set_visible(self, visible: bool):
        self.title_lbl.setVisible(visible)
        self.badge_lbl.setVisible(visible)
```

#### B. Deterministic Geometry Layout (`_position_children` / arithmetic layout)
In `_position_children`:
```python
avail_title_w = WIDTH - 2 * PAD - BADGE_WIDTH - 6  # 6px gap

for index in range(n_rows):
    row = self._row_pairs[index]
    row.title_lbl.setGeometry(PAD, y, avail_title_w, ROW_H)
    row.badge_lbl.setGeometry(WIDTH - PAD - BADGE_WIDTH, y, BADGE_WIDTH, ROW_H)
    y += ROW_H
```

#### C. Live Content & In-Place Tick Updates
In `_apply_content()`:
1. Query `goals = self._store.top_goals(MAX_ROWS)`.
2. For each goal:
   - Compute `st = calculate_deadline_status(goal["deadline"], horizon="today")`.
   - Update `title_lbl.setText(...)` with `fm.elidedText("• " + goal["title"], ..., avail_title_w)`.
   - Update `badge_lbl.setText(st.label)`.
   - Update `badge_lbl.setProperty("urgency", st.level)` and re-polish if changed:
     ```python
     if badge_lbl.property("urgency") != st.level:
         badge_lbl.setProperty("urgency", st.level)
         badge_lbl.style().unpolish(badge_lbl)
         badge_lbl.style().polish(badge_lbl)
     ```
3. On every clock tick (via `_on_tick` or periodic 30-60s update):
   Iterate active rows and recalculate `calculate_deadline_status` in-place so countdown badges tick (`59m left` -> `58m left` -> `Overdue`) without rebuilding the overlay or resizing.

---

### 2.5. `mgt/ui_main.py`

#### A. Dynamic Horizon Dropdown Behavior
When switching tabs (`_on_select_horizon(horizon)`):
```python
allow_choice, default_ts, options = get_horizon_deadline_config(horizon)

self.combo_deadline.blockSignals(True)
self.combo_deadline.clear()
for label, key in options:
    self.combo_deadline.addItem(label, key)
self.combo_deadline.blockSignals(False)

if allow_choice:
    self.combo_deadline.setEnabled(True)
    self.combo_deadline.setVisible(True)
    self.lbl_auto_deadline.setVisible(False)
else:
    # Hide or lock dropdown, show subtle auto-assigned label
    self.combo_deadline.setVisible(False)
    self.lbl_auto_deadline.setText(options[0][0] if options else "")
    self.lbl_auto_deadline.setVisible(True)
```

#### B. Adding Tasks with Default Deadlines
In `_add_task()`:
- If `allow_choice` is True: compute deadline from selected preset (or fallback to `default_ts` if none chosen).
- If `allow_choice` is False: directly use `default_ts`.

---

## 3. Test Suite & Verification Checklist

### New Unit Tests to Add:
1. **`tests/test_timeutil.py`:**
   - Test `compute_preset("eoy")` returns Dec 31 23:59:59.
   - Test `get_horizon_deadline_config("today")` filters out presets beyond today.
   - Test `get_horizon_deadline_config("week")` locks selection and returns Sunday 23:59:59.
2. **`tests/test_database.py`:**
   - Test `rollover_horizons` on Saturday (`date(2026, 10, 10)`): verify `week` tasks move to `tomorrow`.
   - Test `rollover_horizons` on Sunday (`date(2026, 10, 11)`): verify `week` tasks move to `today`.
   - Test `top_goals(n)` returns goal dictionaries with deadlines.
3. **`tests/test_smoke_offscreen.py`:**
   - Verify `OverlayWidget` instantiates, populates goals with deadline badges, and renders all urgency levels without Qt warnings.

### Verification Commands (Run via `uv`):
```powershell
# 1. Run full test suite:
uv run pytest -v

# 2. Run offscreen smoke test:
uv run python -c "import sys; from PyQt6.QtWidgets import QApplication; app = QApplication(sys.argv); from mgt.store import Store; from mgt.pomodoro import PomodoroController; from mgt.ui_overlay import OverlayWidget; s = Store(); p = PomodoroController(); o = OverlayWidget(s, p); o.show(); print('OVERLAY WITH BADGES OK'); sys.exit(0)"
```

---

## 4. Execution Directives for Claude Code
1. Adhere strictly to token and cost rules in `CLAUDE.md`.
2. Make surgical search-and-replace edits only.
3. Run `uv run pytest` after changes to confirm all tests pass.
