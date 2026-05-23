"""Spec 6b TASK 2 — cloud-counter card week/month aggregation tests.

Verifies that `_build_evaluator` returns calls_today / calls_this_week /
calls_this_month independent of the page-level range selector, using
calendar-aligned windows (Monday-onwards for week, 1st-onwards for month).

Tests pin "today" to Wednesday May 13, 2026 via monkeypatched
current_agent_day() so the boundary behavior is deterministic
regardless of when the suite runs.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dashboard.backend.routes import pipeline as mod  # noqa: E402
from engine.persistence.tracker import Tracker  # noqa: E402

TORONTO = ZoneInfo("America/Toronto")


def _write_log(path: Path, timestamps_iso) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for ts in timestamps_iso:
            f.write(
                json.dumps({
                    "timestamp": ts,
                    "model": "gemma4-31b",
                    "status": "ok",
                }) + "\n"
            )


def _write_log_with_tokens(path: Path, entries) -> None:
    """Like _write_log but each entry is (ts_iso, input_tokens,
    output_tokens). For Spec 6b TASK 6 token-aggregation tests."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for ts, in_t, out_t in entries:
            f.write(
                json.dumps({
                    "timestamp": ts,
                    "model": "gemma4-31b",
                    "status": "ok",
                    "input_tokens": in_t,
                    "output_tokens": out_t,
                }) + "\n"
            )


def _toronto_iso(day: date, hour: int = 12) -> str:
    """ISO UTC timestamp for the given day at `hour` Toronto local.

    Midday Toronto is far from the 3 AM agent-day boundary, so all
    test timestamps unambiguously bin to their calendar day's
    agent-day.
    """
    dt = datetime.combine(
        day, datetime.min.time().replace(hour=hour), tzinfo=TORONTO,
    )
    return dt.astimezone(timezone.utc).isoformat()


@pytest.fixture
def fake_today(monkeypatch):
    """Pin 'today' to Wednesday May 13, 2026 (calendar week starts
    Mon May 11; calendar month starts May 1)."""
    fake = date(2026, 5, 13)
    monkeypatch.setattr(mod, "current_agent_day", lambda: fake)
    return fake


def test_build_evaluator_includes_week_and_month(
    tmp_path, monkeypatch, fake_today,
):
    """Backend returns calls_today + calls_this_week + calls_this_month
    populated with the correct aggregations."""
    db = tmp_path / "tracker.db"
    tracker = Tracker(profile_id="test", db_path=db)
    try:
        log = tmp_path / "cloud_eval_usage.jsonl"
        # today = Wed May 13, 2026
        # week  = Mon May 11 .. Wed May 13
        # month = May 1 .. May 13
        _write_log(log, [
            _toronto_iso(date(2026, 5, 13)),  # today
            _toronto_iso(date(2026, 5, 11)),  # Mon (this week)
            _toronto_iso(date(2026, 5, 5)),   # earlier May (month, not week)
            _toronto_iso(date(2026, 4, 30)),  # April (none)
        ])
        monkeypatch.setattr(mod, "_usage_log_path", lambda t: log)
        ev = mod._build_evaluator(tracker, fake_today, fake_today)
        assert ev.calls_today == 1
        assert ev.calls_this_week == 2     # today + Mon May 11
        assert ev.calls_this_month == 3    # today + Mon May 11 + May 5
        assert ev.budget == 1500
    finally:
        tracker.close()


def test_build_evaluator_cloud_counts_independent_of_range(
    tmp_path, monkeypatch, fake_today,
):
    """Whether the route is called with range=today or a 30-day range,
    cloud counts are always today/this-week/this-month — independent
    of the start/end parameters."""
    db = tmp_path / "tracker.db"
    tracker = Tracker(profile_id="test", db_path=db)
    try:
        log = tmp_path / "cloud_eval_usage.jsonl"
        _write_log(log, [
            _toronto_iso(date(2026, 5, 13)),
            _toronto_iso(date(2026, 5, 11)),
        ])
        monkeypatch.setattr(mod, "_usage_log_path", lambda t: log)
        ev_today = mod._build_evaluator(tracker, fake_today, fake_today)
        month_start = fake_today - timedelta(days=30)
        ev_month = mod._build_evaluator(tracker, month_start, fake_today)
        assert ev_today.calls_today == ev_month.calls_today
        assert ev_today.calls_this_week == ev_month.calls_this_week
        assert ev_today.calls_this_month == ev_month.calls_this_month
    finally:
        tracker.close()


def test_build_evaluator_empty_log(
    tmp_path, monkeypatch, fake_today,
):
    """With no usage log present, all three counts are 0 and the
    daily budget is still surfaced."""
    db = tmp_path / "tracker.db"
    tracker = Tracker(profile_id="test", db_path=db)
    try:
        monkeypatch.setattr(
            mod, "_usage_log_path", lambda t: tmp_path / "missing.jsonl"
        )
        ev = mod._build_evaluator(tracker, fake_today, fake_today)
        assert ev.calls_today == 0
        assert ev.calls_this_week == 0
        assert ev.calls_this_month == 0
        assert ev.budget == 1500
    finally:
        tracker.close()


def test_build_evaluator_includes_token_aggregations(
    tmp_path, monkeypatch, fake_today,
):
    """Spec 6b TASK 6 (PATH 6A-cloud-only): cloud token totals are
    aggregated using the same calendar windows as call counts
    (today / this calendar week / this calendar month)."""
    db = tmp_path / "tracker.db"
    tracker = Tracker(profile_id="test", db_path=db)
    try:
        log = tmp_path / "cloud_eval_usage.jsonl"
        # today=Wed May 13, week=Mon May 11.., month=May 1..
        _write_log_with_tokens(log, [
            (_toronto_iso(date(2026, 5, 13)), 1000, 100),  # today
            (_toronto_iso(date(2026, 5, 13)), 2000, 200),  # today (2nd)
            (_toronto_iso(date(2026, 5, 11)), 1500, 150),  # Mon (week)
            (_toronto_iso(date(2026, 5, 5)),  3000, 300),  # May (month, not week)
            (_toronto_iso(date(2026, 4, 30)), 9999, 9999), # April (none)
        ])
        monkeypatch.setattr(mod, "_usage_log_path", lambda t: log)
        ev = mod._build_evaluator(tracker, fake_today, fake_today)
        # today  = 2 calls    -> in 1000+2000=3000, out 100+200=300
        assert ev.tokens.today_input == 3000
        assert ev.tokens.today_output == 300
        # week   = today + Mon -> in 4500, out 450
        assert ev.tokens.this_week_input == 4500
        assert ev.tokens.this_week_output == 450
        # month  = week + May5 -> in 7500, out 750
        assert ev.tokens.this_month_input == 7500
        assert ev.tokens.this_month_output == 750
        # Calls also include both today entries
        assert ev.calls_today == 2
        assert ev.calls_this_week == 3
        assert ev.calls_this_month == 4
    finally:
        tracker.close()


def test_calendar_boundaries_are_correct(
    tmp_path, monkeypatch, fake_today,
):
    """Calendar boundary verification:

    today = Wed May 13, 2026
      -> week_start = Mon May 11
      -> month_start = May 1

    | Timestamp        | today | week | month |
    | ---------------- | ----- | ---- | ----- |
    | Apr 30 (prev mo) |   N   |  N   |   N   |
    | May 1  (1st)     |   N   |  N   |   Y   |
    | May 10 (Sun)     |   N   |  N   |   Y   |
    | May 11 (Mon)     |   N   |  Y   |   Y   |
    | May 13 (today)   |   Y   |  Y   |   Y   |

    Expected: today=1, week=2, month=4.
    """
    db = tmp_path / "tracker.db"
    tracker = Tracker(profile_id="test", db_path=db)
    try:
        log = tmp_path / "cloud_eval_usage.jsonl"
        _write_log(log, [
            _toronto_iso(date(2026, 4, 30)),
            _toronto_iso(date(2026, 5, 1)),
            _toronto_iso(date(2026, 5, 10)),
            _toronto_iso(date(2026, 5, 11)),
            _toronto_iso(date(2026, 5, 13)),
        ])
        monkeypatch.setattr(mod, "_usage_log_path", lambda t: log)
        ev = mod._build_evaluator(tracker, fake_today, fake_today)
        assert ev.calls_today == 1, "only May 13 is today"
        assert ev.calls_this_week == 2, "May 11 + May 13"
        assert ev.calls_this_month == 4, "May 1 + May 10 + May 11 + May 13"
    finally:
        tracker.close()
