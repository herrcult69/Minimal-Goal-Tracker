"""Deadline status and preset math (Qt-free)."""

from datetime import datetime

import pytest

from mgt import timeutil
from mgt.timeutil import calculate_deadline_status, compute_preset

NOW = 1_700_000_000  # fixed epoch for determinism


def test_no_deadline_is_none():
    st = calculate_deadline_status(None, "today", NOW)
    assert st == ("", "", "none")
    assert st.label == "" and st.color == "" and st.level == "none"


def test_overdue():
    st = calculate_deadline_status(NOW - 1, "today", NOW)
    assert st.level == "overdue" and st.label == "Overdue" and st.color == "#ff5252"


def test_critical_under_one_hour():
    assert calculate_deadline_status(NOW + 90, "today", NOW).label == "1m left"
    assert calculate_deadline_status(NOW + 30, "today", NOW).label == "1m left"  # floor max(1, 0)
    assert calculate_deadline_status(NOW + 59 * 60, "today", NOW).label == "59m left"
    assert calculate_deadline_status(NOW + 90, "today", NOW).level == "critical"


def test_urgent_under_24h():
    assert calculate_deadline_status(NOW + 3600, "today", NOW).label == "1.0h left"
    assert calculate_deadline_status(NOW + 5 * 3600, "today", NOW).label == "5.0h left"
    assert calculate_deadline_status(NOW + 5 * 3600, "today", NOW).level == "urgent"
    assert calculate_deadline_status(NOW + 5 * 3600, "today", NOW).color == "#ffb300"


def test_approaching_under_72h():
    assert calculate_deadline_status(NOW + 86400, "week", NOW).label == "1.0d left"
    assert calculate_deadline_status(NOW + 2 * 86400, "week", NOW).label == "2.0d left"
    assert calculate_deadline_status(NOW + 86400, "week", NOW).level == "approaching"


def test_normal_far_future_shows_date():
    deadline = NOW + 10 * 86400
    st = calculate_deadline_status(deadline, "year", NOW)
    assert st.level == "normal" and st.color == "#888888"
    assert st.label == datetime.fromtimestamp(deadline).strftime("%m-%d")


def test_life_horizon_shows_date_not_countdown():
    st = calculate_deadline_status(NOW + 60, "life", NOW)
    assert st.level == "normal"
    assert st.label == datetime.fromtimestamp(NOW + 60).strftime("%m-%d")


def test_life_horizon_overdue_still_overdue():
    st = calculate_deadline_status(NOW - 60, "life", NOW)
    assert st.level == "overdue"


def test_unpacks_as_three_tuple():
    label, color, level = calculate_deadline_status(NOW + 60, "today", NOW)
    assert (label, color, level) == ("1m left", "#ff5252", "critical")


def test_preset_none():
    assert compute_preset("none", datetime(2026, 10, 8, 14, 30)) is None


def test_preset_plus_hours():
    now = datetime(2026, 10, 8, 14, 30)
    assert compute_preset("plus_1h", now) == int(datetime(2026, 10, 8, 15, 30).timestamp())
    assert compute_preset("plus_2h", now) == int(datetime(2026, 10, 8, 16, 30).timestamp())
    assert compute_preset("plus_4h", now) == int(datetime(2026, 10, 8, 18, 30).timestamp())


def test_preset_eod_today_and_tomorrow():
    now = datetime(2026, 10, 8, 14, 30)
    assert compute_preset("eod_today", now) == int(datetime(2026, 10, 8, 23, 59).timestamp())
    assert compute_preset("eod_tomorrow", now) == int(datetime(2026, 10, 9, 23, 59).timestamp())


def test_preset_eow_from_thursday():
    # 2026-10-08 is a Thursday -> next Sunday is 2026-10-11.
    now = datetime(2026, 10, 8, 9, 0)
    assert now.weekday() == 3
    assert compute_preset("eow", now) == int(datetime(2026, 10, 11, 23, 59).timestamp())


def test_preset_eow_on_sunday_is_today():
    sunday = datetime(2026, 10, 11, 9, 0)
    assert sunday.weekday() == 6
    assert compute_preset("eow", sunday) == int(datetime(2026, 10, 11, 23, 59).timestamp())


def test_preset_eom():
    now = datetime(2026, 10, 8, 14, 30)
    assert compute_preset("eom", now) == int(datetime(2026, 10, 31, 23, 59).timestamp())


def test_preset_unknown_raises():
    with pytest.raises(ValueError):
        compute_preset("bogus", datetime(2026, 10, 8, 14, 30))


def test_preset_keys_are_all_handled():
    now = datetime(2026, 10, 8, 14, 30)
    for key in timeutil.PRESET_KEYS:
        compute_preset(key, now)  # must not raise


def test_preset_eoy():
    now = datetime(2026, 10, 8, 14, 30)
    assert compute_preset("eoy", now) == int(datetime(2026, 12, 31, 23, 59).timestamp())


def test_get_horizon_deadline_config_today_filters_valid():
    """today horizon only shows presets that don't spill past today 23:59."""
    now = datetime(2026, 10, 8, 14, 30)
    allow, default, options = timeutil.get_horizon_deadline_config("today", now)
    assert allow is True
    assert default == compute_preset("eod_today", now)
    # All options should be <= eod_today
    for label, key in options:
        ts = compute_preset(key, now)
        assert ts <= default, f"{label} ({ts}) > eod ({default})"


def test_get_horizon_deadline_config_week_locks():
    """week horizon is auto-assigned, no user choice."""
    now = datetime(2026, 10, 8, 14, 30)
    allow, default, options = timeutil.get_horizon_deadline_config("week", now)
    assert allow is False
    assert default == compute_preset("eow", now)
    assert len(options) == 1
    assert "eow" in options[0][1]
