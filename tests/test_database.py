"""SQLite layer CRUD, settings, ordering, and rollover (Qt-free)."""

from datetime import date

import pytest

from mgt.database import (
    add_goal,
    close_db,
    delete_goal,
    get_goals,
    get_setting,
    init_db,
    journal_mode,
    rollover_horizons,
    set_setting,
    toggle_goal_completion,
    top_goal_titles,
    top_goals,
)


@pytest.fixture()
def db(tmp_path):
    init_db(tmp_path / "goals.db")
    yield
    close_db()


def _force_rollover(day: date) -> None:
    """Roll over for a pinned date, ignoring init_db's stamp for the real today.

    ``init_db`` runs ``rollover_horizons(date.today())``, which stamps
    ``last_rollover_date``. Without clearing it, a pinned weekend date that
    happens to equal the machine date would be treated as already rolled over
    and the assertion would depend on when the suite runs.
    """
    set_setting("last_rollover_date", "")
    rollover_horizons(day)


def test_schema_version_and_defaults(db):
    assert journal_mode() == "wal"
    assert get_setting("pomodoro_focus_min") == "25"
    assert get_setting("pomodoro_break_min") == "5"
    assert get_setting("overlay_enabled") == "1"
    assert get_setting("selected_horizon") == "today"
    assert get_setting("missing_key", "fallback") == "fallback"


def test_add_and_get_goal(db):
    gid = add_goal("Write report", "today")
    rows = get_goals("today")
    assert len(rows) == 1
    assert rows[0]["id"] == gid
    assert rows[0]["title"] == "Write report"
    assert rows[0]["is_completed"] == 0


def test_add_goal_trims_title(db):
    add_goal("  padded  ", "today")
    assert get_goals("today")[0]["title"] == "padded"


def test_add_goal_rejects_empty(db):
    with pytest.raises(ValueError):
        add_goal("", "today")
    with pytest.raises(ValueError):
        add_goal("   ", "today")


def test_add_goal_rejects_bad_horizon(db):
    with pytest.raises(ValueError):
        add_goal("x", "someday")


def test_toggle_completion_roundtrip(db):
    gid = add_goal("Task", "today")
    assert toggle_goal_completion(gid) is True
    assert get_goals("today") == []  # hidden by default
    shown = get_goals("today", show_completed=True)
    assert len(shown) == 1 and shown[0]["is_completed"] == 1
    assert shown[0]["completed_at"] is not None
    assert toggle_goal_completion(gid) is False
    assert get_goals("today")[0]["is_completed"] == 0


def test_toggle_missing_returns_false(db):
    assert toggle_goal_completion(9999) is False


def test_delete_goal(db):
    gid = add_goal("Temp", "today")
    assert delete_goal(gid) is True
    assert get_goals("today") == []
    assert delete_goal(gid) is False


def test_ordering_by_deadline_then_id(db):
    add_goal("later", "today", deadline=2000)
    add_goal("none", "today")
    add_goal("sooner", "today", deadline=1000)
    titles = [g["title"] for g in get_goals("today")]
    assert titles == ["sooner", "later", "none"]


def test_get_goals_filters_by_horizon(db):
    add_goal("t", "today")
    add_goal("w", "week")
    assert [g["title"] for g in get_goals("week")] == ["w"]
    assert len(get_goals()) == 2  # no horizon filter -> all


def test_show_completed_caps_at_50(db):
    for i in range(55):
        gid = add_goal(f"done {i}", "today")
        toggle_goal_completion(gid)
    rows = get_goals("today", show_completed=True)
    assert len(rows) == 50
    assert all(r["is_completed"] == 1 for r in rows)


def test_top_goal_titles_priority_and_limit(db):
    add_goal("no deadline", "today")
    add_goal("soon", "today", deadline=1000)
    add_goal("later", "today", deadline=5000)
    add_goal("other horizon", "week", deadline=1)
    assert top_goal_titles(10) == ["soon", "later", "no deadline"]
    assert top_goal_titles(2) == ["soon", "later"]


def test_top_goal_titles_excludes_completed(db):
    gid = add_goal("done", "today", deadline=1)
    add_goal("open", "today", deadline=2)
    toggle_goal_completion(gid)
    assert top_goal_titles(10) == ["open"]


def test_rollover_moves_tomorrow_and_is_idempotent(db):
    gid = add_goal("carry over", "tomorrow")
    rollover_horizons(date(2030, 1, 1))
    assert get_goals("today")[0]["id"] == gid
    assert get_goals("tomorrow") == []
    # second call with the same date is a no-op
    rollover_horizons(date(2030, 1, 1))
    assert len(get_goals("today")) == 1


def test_rollover_leaves_completed_tomorrow(db):
    gid = add_goal("finished early", "tomorrow")
    toggle_goal_completion(gid)
    rollover_horizons(date(2030, 1, 1))
    assert get_goals("today") == []
    assert len(get_goals("tomorrow", show_completed=True)) == 1


def test_settings_roundtrip(db):
    set_setting("overlay_x", "123")
    assert get_setting("overlay_x") == "123"
    set_setting("overlay_x", "456")
    assert get_setting("overlay_x") == "456"


def test_top_goals_returns_deadlines(db):
    gid1 = add_goal("soon", "today", deadline=1000)
    gid2 = add_goal("later", "today", deadline=5000)
    add_goal("no deadline", "today")
    goals = top_goals(10)
    assert len(goals) == 3
    assert goals[0]["id"] == gid1
    assert goals[0]["deadline"] == 1000
    assert goals[2]["deadline"] is None


def test_rollover_week_to_tomorrow_on_saturday(db):
    """Saturday: week goals cascade to tomorrow."""
    gid = add_goal("week task", "week")
    _force_rollover(date(2026, 10, 10))  # Saturday
    goals_week = get_goals("week")
    goals_tomorrow = get_goals("tomorrow")
    assert gid not in [g["id"] for g in goals_week]
    assert any(g["id"] == gid for g in goals_tomorrow)


def test_rollover_week_to_today_on_sunday(db):
    """Sunday: week goals cascade directly to today."""
    gid = add_goal("week task", "week")
    _force_rollover(date(2026, 10, 11))  # Sunday
    goals_week = get_goals("week")
    goals_today = get_goals("today")
    assert gid not in [g["id"] for g in goals_week]
    assert any(g["id"] == gid for g in goals_today)
