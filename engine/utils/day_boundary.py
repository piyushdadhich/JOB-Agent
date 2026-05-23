"""Single source of truth for the agent's "day" boundary.

A day starts at DAY_BOUNDARY_HOUR Toronto time. Default 3 AM
Toronto = midnight Pacific = Google's free-tier quota reset.
Anchoring everything to this boundary keeps the dashboard
counters, the cloud evaluator's daily SOFT_STOP, and the daily
pipeline's "today" all in sync.

Configurable per profile via config/profiles/{profile}.yaml:

    agent:
      day_boundary_hour: 3

DST handling: the boundary is "3 AM Toronto" in *local* terms, so
on spring-forward day the agent day starts at 3 AM EDT (= 7 AM
UTC) and on fall-back day it starts at 3 AM EST (= 8 AM UTC).
zoneinfo.ZoneInfo("America/Toronto") handles this automatically;
we never hardcode a UTC offset.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple

import yaml
from zoneinfo import ZoneInfo

TORONTO = ZoneInfo("America/Toronto")
DEFAULT_BOUNDARY_HOUR = 3

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def get_boundary_hour(profile_id: Optional[str] = None) -> int:
    """Resolve the day-boundary hour from profile config, falling
    back to DEFAULT_BOUNDARY_HOUR (3) if no profile or no setting.
    """
    if not profile_id:
        return DEFAULT_BOUNDARY_HOUR
    path = (
        PROJECT_ROOT / "config" / "profiles" / f"{profile_id}.yaml"
    )
    if not path.exists():
        return DEFAULT_BOUNDARY_HOUR
    try:
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        agent_cfg = cfg.get("agent") or {}
        return int(
            agent_cfg.get("day_boundary_hour", DEFAULT_BOUNDARY_HOUR)
        )
    except Exception:
        return DEFAULT_BOUNDARY_HOUR


def _to_toronto(now: Optional[datetime]) -> datetime:
    if now is None:
        return datetime.now(TORONTO)
    if now.tzinfo is None:
        # Treat naive as Toronto local; safer than assuming UTC.
        return now.replace(tzinfo=TORONTO)
    return now.astimezone(TORONTO)


def current_agent_day(
    now: Optional[datetime] = None,
    boundary_hour: int = DEFAULT_BOUNDARY_HOUR,
) -> date:
    """The agent's "current day".

    A day starts at boundary_hour Toronto. At 02:59 Toronto the
    current agent-day is still yesterday's; at 03:00 today starts.
    """
    toronto_now = _to_toronto(now)
    if toronto_now.hour < boundary_hour:
        return (toronto_now - timedelta(days=1)).date()
    return toronto_now.date()


def entry_agent_day(
    ts: datetime, boundary_hour: int = DEFAULT_BOUNDARY_HOUR,
) -> date:
    """Bin an aware (or naive-UTC) timestamp into its agent-day.

    Naive timestamps are treated as UTC so that the JSONL usage
    log -- which writes UTC ISO -- bins correctly without the
    caller having to remember to attach a timezone.
    """
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    toronto_ts = ts.astimezone(TORONTO)
    if toronto_ts.hour < boundary_hour:
        return (toronto_ts - timedelta(days=1)).date()
    return toronto_ts.date()


def agent_day_range(
    range_key: str,
    now: Optional[datetime] = None,
    boundary_hour: int = DEFAULT_BOUNDARY_HOUR,
) -> Tuple[date, date, date, date]:
    """Return (period_start, period_end, prev_start, prev_end).

    Periods are rolling windows ending on the current agent-day:
      today: 1 day; week: 7 days; month: 30 days. The previous
    period is the same length immediately preceding it.
    """
    end = current_agent_day(now=now, boundary_hour=boundary_hour)
    if range_key == "today":
        days = 1
    elif range_key == "week":
        days = 7
    elif range_key == "month":
        days = 30
    else:
        raise ValueError(
            f"Invalid range {range_key!r}; expected today|week|month"
        )
    period_end = end
    period_start = end - timedelta(days=days - 1)
    prev_end = period_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)
    return period_start, period_end, prev_start, prev_end


def calendar_week_start(day: date) -> date:
    """Return the Monday of the calendar week containing `day`.

    Used by the dashboard cloud-counter card for "this week" semantics
    that match a human's mental model (Monday-onwards, not rolling 7d).
    Python's date.weekday() returns 0 for Monday and 6 for Sunday.
    """
    return day - timedelta(days=day.weekday())


def calendar_month_start(day: date) -> date:
    """Return the 1st of the calendar month containing `day`.

    Used by the dashboard cloud-counter card for "this month" semantics
    that match a human's mental model (1st-onwards, not rolling 30d).
    """
    return day.replace(day=1)


def agent_day_window(
    day: date, boundary_hour: int = DEFAULT_BOUNDARY_HOUR,
) -> Tuple[datetime, datetime]:
    """(start_dt, end_dt) Toronto-aware datetimes spanning an
    agent-day. End is exclusive. Useful for range comparison
    against UTC ISO columns (Playwright's set_input_files /
    SQL >= start AND < end).
    """
    start_toronto = datetime.combine(
        day, time(boundary_hour, 0), tzinfo=TORONTO,
    )
    end_toronto = start_toronto + timedelta(days=1)
    return start_toronto, end_toronto


def agent_day_range_utc_iso(
    start_date: date,
    end_date: date,
    boundary_hour: int = DEFAULT_BOUNDARY_HOUR,
) -> Tuple[str, str]:
    """Convert an inclusive agent-day range to UTC ISO datetime
    bounds for SQL `col >= ? AND col < ?` comparison against the
    UTC ISO timestamp columns (date_discovered, evaluated_at,
    selected_at, etc.).

    Example with default 3 AM Toronto boundary on EDT:
        agent_day_range_utc_iso(date(2026,5,8), date(2026,5,8))
        -> ("2026-05-08T07:00:00+00:00",
            "2026-05-09T07:00:00+00:00")

    The end is the START of the day AFTER end_date (exclusive),
    so the range covers the full inclusive [start_date, end_date]
    span without overlapping the next agent-day.

    DST is handled by ZoneInfo automatically: on spring-forward
    the UTC offset changes from -5 to -4, so the same 3 AM
    Toronto boundary lands on a different UTC time. Tested via
    test_agent_day_range_utc_iso_handles_dst.
    """
    start_local = datetime.combine(
        start_date, time(boundary_hour, 0), tzinfo=TORONTO,
    )
    end_local = datetime.combine(
        end_date + timedelta(days=1),
        time(boundary_hour, 0),
        tzinfo=TORONTO,
    )
    return (
        start_local.astimezone(timezone.utc).isoformat(),
        end_local.astimezone(timezone.utc).isoformat(),
    )
