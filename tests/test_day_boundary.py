"""Unit tests for engine.utils.day_boundary."""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.utils.day_boundary import (  # noqa: E402
    DEFAULT_BOUNDARY_HOUR,
    TORONTO,
    agent_day_range,
    agent_day_range_utc_iso,
    agent_day_window,
    current_agent_day,
    entry_agent_day,
    get_boundary_hour,
)


# --- current_agent_day ------------------------------------------

def test_before_3am_returns_yesterday():
    """At 02:30 Toronto May 9, the current agent-day is May 8."""
    now = datetime(2026, 5, 9, 2, 30, tzinfo=TORONTO)
    assert current_agent_day(now=now) == date(2026, 5, 8)


def test_at_3am_returns_today():
    """At exactly 03:00 Toronto May 9, the current agent-day is May 9."""
    now = datetime(2026, 5, 9, 3, 0, tzinfo=TORONTO)
    assert current_agent_day(now=now) == date(2026, 5, 9)


def test_after_3am_returns_today():
    """At 03:30 Toronto May 9, the current agent-day is May 9."""
    now = datetime(2026, 5, 9, 3, 30, tzinfo=TORONTO)
    assert current_agent_day(now=now) == date(2026, 5, 9)


def test_late_evening_returns_today():
    """At 21:00 Toronto May 8, the current agent-day is still May 8.

    This is the original bug case -- the old UTC-anchored code
    returned May 9 here because UTC was already May 9.
    """
    now = datetime(2026, 5, 8, 21, 0, tzinfo=TORONTO)
    assert current_agent_day(now=now) == date(2026, 5, 8)


def test_accepts_utc_now():
    """UTC datetime gets converted to Toronto."""
    now_utc = datetime(2026, 5, 9, 1, 30, tzinfo=timezone.utc)
    # 1:30 UTC May 9 = 21:30 Toronto May 8 (EDT) -> agent day May 8.
    assert current_agent_day(now=now_utc) == date(2026, 5, 8)


def test_custom_boundary_hour():
    """Override default 3 AM with a different boundary."""
    now = datetime(2026, 5, 9, 4, 30, tzinfo=TORONTO)
    assert current_agent_day(now=now, boundary_hour=5) == date(2026, 5, 8)
    assert current_agent_day(now=now, boundary_hour=4) == date(2026, 5, 9)


# --- entry_agent_day --------------------------------------------

def test_entry_agent_day_bins_utc_timestamp():
    """A UTC timestamp at 19:00 May 8 = 15:00 Toronto May 8."""
    ts = datetime(2026, 5, 8, 19, 0, tzinfo=timezone.utc)
    assert entry_agent_day(ts) == date(2026, 5, 8)


def test_entry_agent_day_late_evening_still_in_day():
    """01:30 UTC May 9 = 21:30 EDT May 8 -> agent day May 8."""
    ts = datetime(2026, 5, 9, 1, 30, tzinfo=timezone.utc)
    assert entry_agent_day(ts) == date(2026, 5, 8)


def test_entry_agent_day_after_3am_toronto_advances():
    """07:30 UTC May 9 = 03:30 EDT May 9 -> agent day May 9."""
    ts = datetime(2026, 5, 9, 7, 30, tzinfo=timezone.utc)
    assert entry_agent_day(ts) == date(2026, 5, 9)


def test_entry_agent_day_naive_treated_as_utc():
    """Naive timestamp (e.g. from JSONL parse) is assumed UTC."""
    naive = datetime(2026, 5, 8, 19, 0)
    assert entry_agent_day(naive) == date(2026, 5, 8)


# --- agent_day_range --------------------------------------------

def test_agent_day_range_today():
    now = datetime(2026, 5, 8, 15, 0, tzinfo=TORONTO)
    p_start, p_end, prev_start, prev_end = agent_day_range(
        "today", now=now,
    )
    assert p_start == date(2026, 5, 8)
    assert p_end == date(2026, 5, 8)
    assert prev_start == date(2026, 5, 7)
    assert prev_end == date(2026, 5, 7)


def test_agent_day_range_week():
    now = datetime(2026, 5, 8, 15, 0, tzinfo=TORONTO)
    p_start, p_end, prev_start, prev_end = agent_day_range(
        "week", now=now,
    )
    assert p_end == date(2026, 5, 8)
    assert p_start == date(2026, 5, 2)
    assert prev_end == date(2026, 5, 1)
    assert prev_start == date(2026, 4, 25)


def test_agent_day_range_month():
    now = datetime(2026, 5, 8, 15, 0, tzinfo=TORONTO)
    p_start, p_end, _, _ = agent_day_range("month", now=now)
    assert p_end == date(2026, 5, 8)
    assert p_start == date(2026, 4, 9)


def test_agent_day_range_invalid_raises():
    with pytest.raises(ValueError, match="Invalid range"):
        agent_day_range("yesterday")


def test_agent_day_range_at_2am_uses_yesterday_as_today():
    """At 02:30 Toronto May 9, agent_day_range('today') returns May 8."""
    now = datetime(2026, 5, 9, 2, 30, tzinfo=TORONTO)
    p_start, p_end, _, _ = agent_day_range("today", now=now)
    assert p_start == p_end == date(2026, 5, 8)


# --- agent_day_window -------------------------------------------

def test_agent_day_window_returns_24h_span():
    start, end = agent_day_window(date(2026, 5, 8))
    assert end - start == timedelta(days=1)
    assert start.hour == DEFAULT_BOUNDARY_HOUR
    assert start.tzinfo == TORONTO


def test_agent_day_window_handles_dst_spring_forward():
    """On DST spring-forward (2026-03-08), Toronto loses an hour at
    2 AM. The 3-AM boundary still resolves correctly.
    """
    # The day before DST starts.
    start, end = agent_day_window(date(2026, 3, 7))
    # Window should cover the spring-forward transition.
    assert end - start == timedelta(days=1)
    # End is 03:00 Toronto on March 8 -- the day DST started.
    assert end.date() == date(2026, 3, 8)


# --- agent_day_range_utc_iso ------------------------------------

def test_agent_day_range_utc_iso_single_day_edt():
    """May 8 (EDT, UTC-4): 3 AM Toronto = 7 AM UTC."""
    start_iso, end_iso = agent_day_range_utc_iso(
        date(2026, 5, 8), date(2026, 5, 8),
    )
    assert start_iso == "2026-05-08T07:00:00+00:00"
    assert end_iso == "2026-05-09T07:00:00+00:00"


def test_agent_day_range_utc_iso_multi_day_edt():
    start_iso, end_iso = agent_day_range_utc_iso(
        date(2026, 5, 8), date(2026, 5, 14),
    )
    assert start_iso == "2026-05-08T07:00:00+00:00"
    assert end_iso == "2026-05-15T07:00:00+00:00"


def test_agent_day_range_utc_iso_handles_dst_spring_forward():
    """March 7-9 spans the DST transition (March 8, 2026).
    Mar 7: 3 AM EST = 8 AM UTC.
    Mar 9 (after DST): 3 AM EDT = 7 AM UTC.
    """
    start_iso, end_iso = agent_day_range_utc_iso(
        date(2026, 3, 7), date(2026, 3, 9),
    )
    assert start_iso == "2026-03-07T08:00:00+00:00"  # EST start
    # End is 3 AM Toronto on Mar 10, after DST -> 7 AM UTC.
    assert end_iso == "2026-03-10T07:00:00+00:00"


def test_agent_day_range_utc_iso_captures_late_night_posting():
    """A posting discovered at 06:30 UTC May 9 (= 02:30 EDT May 9,
    agent-day May 8) MUST fall within the May-8 UTC range.

    This is the boundary bug the SQL fix addresses.
    """
    start_iso, end_iso = agent_day_range_utc_iso(
        date(2026, 5, 8), date(2026, 5, 8),
    )
    # The posting's UTC timestamp:
    posting_ts = "2026-05-09T06:30:00+00:00"
    # Lex comparison should put it within the range.
    assert start_iso <= posting_ts < end_iso


def test_agent_day_range_utc_iso_excludes_post_boundary_posting():
    """A posting at 07:30 UTC May 9 = 03:30 EDT May 9 = agent-day
    May 9, NOT May 8. Must fall OUTSIDE the May-8 range."""
    start_iso, end_iso = agent_day_range_utc_iso(
        date(2026, 5, 8), date(2026, 5, 8),
    )
    posting_ts = "2026-05-09T07:30:00+00:00"
    assert not (start_iso <= posting_ts < end_iso)


def test_agent_day_range_utc_iso_custom_boundary_hour():
    """boundary_hour=5 -> 5 AM Toronto = 9 AM UTC (EDT)."""
    start_iso, end_iso = agent_day_range_utc_iso(
        date(2026, 5, 8), date(2026, 5, 8), boundary_hour=5,
    )
    assert start_iso == "2026-05-08T09:00:00+00:00"
    assert end_iso == "2026-05-09T09:00:00+00:00"


# --- get_boundary_hour ------------------------------------------

def test_get_boundary_hour_no_profile_returns_default():
    assert get_boundary_hour(None) == DEFAULT_BOUNDARY_HOUR


def test_get_boundary_hour_missing_profile_returns_default(tmp_path):
    assert get_boundary_hour("nonexistent_profile") == DEFAULT_BOUNDARY_HOUR


def test_get_boundary_hour_reads_default_yaml():
    """default.yaml should have agent.day_boundary_hour: 3 set."""
    assert get_boundary_hour("default") == 3
