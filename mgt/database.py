"""SQLite layer: schema, CRUD, settings, horizon rollover. Qt-free.

One persistent connection is opened in ``init_db()`` and reused; never connect
per call. All SQL is parameterized.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import osenv

SCHEMA_VERSION = 1
HORIZONS = ("today", "tomorrow", "week", "month", "year", "life")

_conn: Optional[sqlite3.Connection] = None

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS goals (
    id           INTEGER PRIMARY KEY,
    title        TEXT    NOT NULL CHECK(length(title) BETWEEN 1 AND 200),
    horizon      TEXT    NOT NULL CHECK(horizon IN ('today','tomorrow','week','month','year','life')),
    deadline     INTEGER,
    is_completed INTEGER NOT NULL DEFAULT 0 CHECK(is_completed IN (0,1)),
    completed_at INTEGER,
    created_at   INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_goals_view ON goals(horizon, is_completed, deadline);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;
"""

_DEFAULTS = {
    "pomodoro_focus_min": "25",
    "pomodoro_break_min": "5",
    "pomodoro_auto_start": "0",
    "overlay_enabled": "1",
    "overlay_on_top": "1",
    "reminders_enabled": "1",
    "selected_horizon": "today",
    "show_completed": "0",
}


def init_db(path: Optional[Path] = None) -> None:
    """Connect, apply pragmas, create the schema, seed defaults, run rollover.

    ``path`` defaults to ``data_dir()/goals.db``; tests may pass a temp path.
    Safe to call repeatedly: any previous connection is closed first.
    """
    global _conn
    close_db()

    db_path = Path(path) if path is not None else osenv.data_dir() / "goals.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), isolation_level=None)  # autocommit
    conn.row_factory = sqlite3.Row
    mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    conn.execute(f"PRAGMA synchronous={'NORMAL' if mode == 'wal' else 'FULL'}")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-512")  # 512 KB page cache
    conn.execute("PRAGMA busy_timeout=2000")
    _conn = conn

    _migrate(conn)
    _seed_defaults(conn)
    rollover_horizons(date.today())


def close_db() -> None:
    global _conn
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
        _conn = None


def _require() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("database not initialized; call init_db() first")
    return _conn


def _migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < 1:
        conn.executescript(_SCHEMA_V1)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    # Future migrations: if version < 2: ... ; then set user_version = 2.


def _seed_defaults(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", _DEFAULTS.items()
    )


def journal_mode() -> str:
    """Current SQLite journal mode, or ``not initialized`` before init_db()."""
    if _conn is None:
        return "not initialized"
    return _conn.execute("PRAGMA journal_mode").fetchone()[0]


# --- settings -------------------------------------------------------------

def get_setting(key: str, default: str = "") -> str:
    row = _require().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row is not None else default


def set_setting(key: str, value: str) -> None:
    _require().execute(
        "INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (key, str(value))
    )


def get_all_settings() -> Dict[str, str]:
    """Whole settings table as a dict; used to prime the Store's cache."""
    rows = _require().execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


# --- goals ----------------------------------------------------------------

def add_goal(title: str, horizon: str, deadline: Optional[int] = None) -> int:
    title = (title or "").strip()
    if not title:
        raise ValueError("title must not be empty")
    if horizon not in HORIZONS:
        raise ValueError(f"invalid horizon: {horizon!r}")
    cur = _require().execute(
        "INSERT INTO goals(title, horizon, deadline) VALUES (?, ?, ?)",
        (title[:200], horizon, deadline),
    )
    return int(cur.lastrowid)


def get_goals(
    horizon: Optional[str] = None, show_completed: bool = False, limit: int = 100
) -> List[Dict[str, Any]]:
    conn = _require()

    conds = []
    base_params: List[Any] = []
    if horizon is not None:
        conds.append("horizon = ?")
        base_params.append(horizon)

    def fetch(extra: str, order: str, lim: int) -> List[Dict[str, Any]]:
        where = " AND ".join(conds + [extra])
        sql = f"SELECT * FROM goals WHERE {where} ORDER BY {order} LIMIT ?"
        rows = conn.execute(sql, base_params + [lim]).fetchall()
        return [dict(r) for r in rows]

    active = fetch("is_completed = 0", "deadline IS NULL, deadline, id", limit)
    if not show_completed:
        return active
    completed = fetch("is_completed = 1", "completed_at DESC, id DESC", 50)
    return active + completed


def toggle_goal_completion(goal_id: int) -> bool:
    conn = _require()
    row = conn.execute("SELECT is_completed FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if row is None:
        return False
    new_state = 0 if row["is_completed"] else 1
    completed_at = int(time.time()) if new_state else None
    conn.execute(
        "UPDATE goals SET is_completed = ?, completed_at = ? WHERE id = ?",
        (new_state, completed_at, goal_id),
    )
    return bool(new_state)


def delete_goal(goal_id: int) -> bool:
    cur = _require().execute("DELETE FROM goals WHERE id = ?", (goal_id,))
    return cur.rowcount > 0


def top_goal_titles(n: int) -> List[str]:
    """Today's uncompleted goals in priority order (overlay and reminders)."""
    rows = _require().execute(
        "SELECT title FROM goals WHERE horizon = 'today' AND is_completed = 0 "
        "ORDER BY deadline IS NULL, deadline, id LIMIT ?",
        (n,),
    ).fetchall()
    return [r["title"] for r in rows]


def top_goals(n: int) -> List[Dict[str, Any]]:
    """Today's uncompleted goals with deadlines for overlay and reminders."""
    rows = _require().execute(
        "SELECT id, title, deadline FROM goals WHERE horizon = 'today' AND is_completed = 0 "
        "ORDER BY deadline IS NULL, deadline ASC, id DESC LIMIT ?",
        (n,),
    ).fetchall()
    return [dict(r) for r in rows]


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
