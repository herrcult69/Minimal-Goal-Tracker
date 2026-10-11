"""Deadline presets and status math. Qt-free and clock-injectable for tests."""

from __future__ import annotations

import calendar
import time
from datetime import datetime, timedelta
from typing import List, NamedTuple, Optional, Tuple

# Colors mirror the QSS badge urgency selectors in mgt/styles.py (§8).
_OVERDUE = "#ff5252"
_URGENT = "#ffb300"
_APPROACHING = "#64b5f6"
_NORMAL = "#888888"

PRESET_KEYS = (
    "none",
    "eod_today",
    "plus_1h",
    "plus_2h",
    "plus_4h",
    "eod_tomorrow",
    "tom_morning",
    "tom_afternoon",
    "eow",
    "eom",
    "eoy",
)


class DeadlineStatus(NamedTuple):
    label: str
    color: str
    level: str


def calculate_deadline_status(
    deadline: Optional[int], horizon: str = "", now: Optional[int] = None
) -> DeadlineStatus:
    """Map a deadline to a (label, color, level) badge.

    ``now`` is epoch seconds; defaults to the current time. The result is a
    NamedTuple, so it still unpacks as v1's 3-tuple.

    Note: a ``life`` horizon with a future deadline renders as a plain local
    ``MM-DD`` date rather than a countdown (§5 table row "or horizon is life").
    An already-overdue life goal still reports ``Overdue``.
    """
    if deadline is None:
        return DeadlineStatus("", "", "none")

    now = int(time.time()) if now is None else int(now)
    delta = deadline - now

    if delta <= 0:
        return DeadlineStatus("Overdue", _OVERDUE, "overdue")

    if horizon == "life":
        return DeadlineStatus(_local_md(deadline), _NORMAL, "normal")

    if delta < 3600:
        return DeadlineStatus(f"{max(1, delta // 60)}m left", _OVERDUE, "critical")
    if delta < 86400:
        return DeadlineStatus(f"{delta / 3600:.1f}h left", _URGENT, "urgent")
    if delta < 259200:  # 72 h
        return DeadlineStatus(f"{delta / 86400:.1f}d left", _APPROACHING, "approaching")
    return DeadlineStatus(_local_md(deadline), _NORMAL, "normal")


def _local_md(epoch: int) -> str:
    return datetime.fromtimestamp(epoch).strftime("%m-%d")


def compute_preset(key: str, now: datetime) -> Optional[int]:
    """Return epoch seconds for a named deadline preset, or None for ``none``.

    Uses the local timezone of ``now`` (naive datetimes are treated as local).
    """
    if key == "none":
        return None
    if key == "plus_1h":
        return _ts(now + timedelta(hours=1))
    if key == "plus_2h":
        return _ts(now + timedelta(hours=2))
    if key == "plus_4h":
        return _ts(now + timedelta(hours=4))
    if key == "eod_today":
        return _ts(_at_2359(now))
    if key == "eod_tomorrow":
        return _ts(_at_2359(now + timedelta(days=1)))
    if key == "tom_morning":
        return _ts((now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0))
    if key == "tom_afternoon":
        return _ts((now + timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0))
    if key == "eow":
        # Sunday 23:59; if today is Sunday, today 23:59.
        days_ahead = (6 - now.weekday()) % 7  # Monday=0 .. Sunday=6
        return _ts(_at_2359(now + timedelta(days=days_ahead)))
    if key == "eom":
        last_day = calendar.monthrange(now.year, now.month)[1]
        return _ts(now.replace(day=last_day, hour=23, minute=59, second=0, microsecond=0))
    if key == "eoy":
        return _ts(now.replace(month=12, day=31, hour=23, minute=59, second=0, microsecond=0))
    raise ValueError(f"unknown preset: {key!r}")


def _at_2359(dt: datetime) -> datetime:
    return dt.replace(hour=23, minute=59, second=0, microsecond=0)


def _ts(dt: datetime) -> int:
    return int(dt.timestamp())


def get_horizon_deadline_config(
    horizon: str, now: Optional[datetime] = None
) -> Tuple[bool, Optional[int], List[Tuple[str, str]]]:
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
