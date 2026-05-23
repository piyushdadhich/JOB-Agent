"""Regression tests for dashboard.backend.routes.pipeline timezone
handling.

After the May-8-vs-May-9 bug, "today" was switched to local time,
then unified across the codebase via engine.utils.day_boundary
(default boundary 3 AM Toronto = midnight Pacific = Google quota
reset). These tests exercise the dashboard's wiring through that
helper.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dashboard.backend.routes import pipeline as mod  # noqa: E402


# --- _today() ----------------------------------------------------

def test_today_delegates_to_current_agent_day(monkeypatch):
    """_today() must delegate to engine.utils.day_boundary -- not
    use UTC, system local, or a hand-rolled formula. This is the
    regression guard."""
    fake = date(2026, 5, 8)
    monkeypatch.setattr(mod, "current_agent_day", lambda: fake)
    assert mod._today() == fake


# --- _resolve_range ---------------------------------------------

def test_resolve_range_today_is_single_day(monkeypatch):
    monkeypatch.setattr(mod, "current_agent_day", lambda: date(2026, 5, 8))
    p_start, p_end, prev_start, prev_end = mod._resolve_range("today")
    assert p_start == date(2026, 5, 8)
    assert p_end == date(2026, 5, 8)
    assert prev_start == date(2026, 5, 7)
    assert prev_end == date(2026, 5, 7)


def test_resolve_range_week_is_7_days(monkeypatch):
    monkeypatch.setattr(mod, "current_agent_day", lambda: date(2026, 5, 8))
    p_start, p_end, prev_start, prev_end = mod._resolve_range("week")
    assert p_end == date(2026, 5, 8)
    assert p_start == date(2026, 5, 2)
    assert prev_end == date(2026, 5, 1)
    assert prev_start == date(2026, 4, 25)


def test_resolve_range_month_is_30_days(monkeypatch):
    monkeypatch.setattr(mod, "current_agent_day", lambda: date(2026, 5, 8))
    p_start, p_end, _, _ = mod._resolve_range("month")
    assert p_end == date(2026, 5, 8)
    assert p_start == date(2026, 4, 9)


# --- _count_cloud_calls_in_range -------------------------------

def _write_log(path: Path, timestamps: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for ts in timestamps:
            f.write(json.dumps({"timestamp": ts, "status": "ok"}) + "\n")


def test_count_cloud_calls_uses_local_date_for_comparison(tmp_path):
    """A UTC timestamp at 17:00Z May 8 is 13:00 May 8 local in
    Toronto / EST / EDT. Should count when start=end=May 8 local."""
    log = tmp_path / "cloud_eval_usage.jsonl"
    _write_log(log, [
        # Clearly within May 8 in any timezone west of UTC.
        "2026-05-08T17:00:00+00:00",
        "2026-05-08T17:30:00+00:00",
    ])
    n = mod._count_cloud_calls_in_range(
        log, date(2026, 5, 8), date(2026, 5, 8),
    )
    assert n == 2


def test_count_cloud_calls_returns_zero_for_missing_log(tmp_path):
    log = tmp_path / "missing.jsonl"
    n = mod._count_cloud_calls_in_range(
        log, date(2026, 5, 8), date(2026, 5, 8),
    )
    assert n == 0


def test_count_cloud_calls_skips_malformed_lines(tmp_path):
    log = tmp_path / "cloud_eval_usage.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "w", encoding="utf-8") as f:
        f.write("\n")
        f.write("not-json\n")
        f.write(json.dumps({"timestamp": ""}) + "\n")
        f.write(json.dumps({"timestamp": "not-a-date"}) + "\n")
        f.write(json.dumps({"timestamp": "2026-05-08T17:00:00+00:00"}) + "\n")
    n = mod._count_cloud_calls_in_range(
        log, date(2026, 5, 8), date(2026, 5, 8),
    )
    assert n == 1
