"""Spec 13 TASK 1 — scheduler tests."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from jobagent import scheduler


@pytest.fixture
def fake_root(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler, "_project_root", lambda: tmp_path)
    return tmp_path


def test_run_invokes_run_daily(fake_root):
    with patch("scripts.run_daily.main", return_value=0) as m:
        record = scheduler.run("default", include_expansion=False)
    m.assert_called_once_with(["--profile", "default"])
    assert record.return_code == 0
    assert record.phases["daily"] == "ok"
    assert record.phases["expansion"] == "skipped"


def test_run_records_failure_exit_code(fake_root):
    with patch("scripts.run_daily.main", return_value=5):
        record = scheduler.run("default", include_expansion=False)
    assert record.return_code == 5
    assert "exit 5" in record.phases["daily"]


def test_run_handles_exception_in_run_daily(fake_root):
    with patch("scripts.run_daily.main", side_effect=RuntimeError("boom")):
        record = scheduler.run("default", include_expansion=False)
    assert record.return_code == 1
    assert "RuntimeError" in record.phases["daily"]


def test_run_includes_expansion_when_forced(fake_root):
    with patch("scripts.run_daily.main", return_value=0), \
         patch("scripts.run_expansion.main", return_value=0) as m:
        record = scheduler.run("default", include_expansion=True)
    m.assert_called_once_with(["--profile", "default"])
    assert record.phases["expansion"] == "ok"


def test_run_writes_history_file(fake_root):
    with patch("scripts.run_daily.main", return_value=0):
        scheduler.run("default", include_expansion=False)
    path = fake_root / "data" / "default" / "run_history.json"
    assert path.exists()
    history = json.loads(path.read_text(encoding="utf-8"))
    assert len(history) == 1
    assert history[0]["return_code"] == 0


def test_run_appends_to_existing_history(fake_root):
    with patch("scripts.run_daily.main", return_value=0):
        scheduler.run("default", include_expansion=False)
        scheduler.run("default", include_expansion=False)
        scheduler.run("default", include_expansion=False)
    path = fake_root / "data" / "default" / "run_history.json"
    history = json.loads(path.read_text(encoding="utf-8"))
    assert len(history) == 3


def test_run_caps_history_at_max(fake_root, monkeypatch):
    monkeypatch.setattr(scheduler, "_MAX_HISTORY", 3)
    with patch("scripts.run_daily.main", return_value=0):
        for _ in range(5):
            scheduler.run("default", include_expansion=False)
    path = fake_root / "data" / "default" / "run_history.json"
    history = json.loads(path.read_text(encoding="utf-8"))
    assert len(history) == 3


def test_last_run_returns_none_when_no_history(fake_root):
    assert scheduler.last_run("default") is None


def test_last_run_returns_most_recent(fake_root):
    with patch("scripts.run_daily.main", return_value=0):
        scheduler.run("default", include_expansion=False)
    last = scheduler.last_run("default")
    assert last is not None
    assert last["return_code"] == 0


def test_next_run_formats_iso_timestamp(fake_root):
    iso = scheduler.next_run("default", schedule_time="02:00")
    # ISO format like 2026-05-19T02:00:00.
    assert "T02:00" in iso


def test_schedule_snapshot_returns_full_payload(fake_root):
    snap = scheduler.schedule_snapshot("default", schedule_time="03:30")
    assert "last_run" in snap
    assert "next_run" in snap
    assert snap["schedule_time"] == "03:30"
