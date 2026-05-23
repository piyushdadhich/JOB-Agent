"""Unit tests for engine.llm.call_manager.

Mocks GemmaCloudClient and the persist + find_postings + get_posting
callbacks — no live API, no file system beyond tmp_path.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.llm import call_manager as cm_mod  # noqa: E402
from engine.llm.call_manager import CallManager  # noqa: E402
from engine.llm.gemma_cloud_client import (  # noqa: E402
    RateLimitedError,
    ServerError,
)


def _posting(pid, date="2026-05-07T10:00:00Z", **kw):
    base = {
        "id": pid,
        "employer": f"Co{pid}",
        "title": f"Title{pid}",
        "location": "Toronto",
        "posting_text": "text",
        "date_discovered": date,
    }
    base.update(kw)
    return base


def _build_manager(
    tmp_path, monkeypatch,
    new_postings=None, used_today=0,
    tracker_postings=None,
):
    monkeypatch.chdir(tmp_path)
    client = MagicMock()
    client.count_today = MagicMock(return_value=used_today)
    persist = MagicMock()
    find = MagicMock(return_value=list(new_postings or []))
    tracker_db = {p["id"]: p for p in (tracker_postings or [])}
    get_posting = MagicMock(side_effect=lambda pid: tracker_db.get(pid))
    return (
        CallManager(
            profile_id="test",
            gemma_client=client,
            find_postings_fn=find,
            get_posting_fn=get_posting,
            persist_fn=persist,
            prompt_template="P={posting_text}",
            inventory_summary="INV",
        ),
        client, persist, find, get_posting,
    )


# --- INVENTORY ----------------------------------------------------

def test_inventory_counts_new_postings(tmp_path, monkeypatch):
    mgr, *_ = _build_manager(
        tmp_path, monkeypatch,
        new_postings=[_posting(1), _posting(2), _posting(3)],
        used_today=10,
    )
    inv = mgr.inventory()
    assert inv["work"]["new_count"] == 3
    assert inv["budget"]["used_today"] == 10
    assert inv["budget"]["available"] == CallManager.SOFT_STOP - 10


def test_inventory_materializes_retry_queue(tmp_path, monkeypatch):
    mgr, *_, get_posting = _build_manager(
        tmp_path, monkeypatch,
        tracker_postings=[_posting(5), _posting(6)],
    )
    mgr.queue_path.write_text(json.dumps([
        {"opportunity_id": 5, "status": "queued",
         "retry_count": 0, "queued_at": "2026-05-05T00:00:00Z"},
        {"opportunity_id": 6, "status": "queued",
         "retry_count": 1, "queued_at": "2026-05-04T00:00:00Z"},
        {"opportunity_id": 7, "status": "abandoned", "retry_count": 2},
    ]), encoding="utf-8")
    inv = mgr.inventory()
    assert inv["work"]["retry_count"] == 2
    ids = sorted(p["id"] for p in inv["work"]["retry_queue"])
    assert ids == [5, 6]
    assert all("_queued_at" in p for p in inv["work"]["retry_queue"])


def test_inventory_drops_retries_missing_from_tracker(tmp_path, monkeypatch):
    mgr, *_ = _build_manager(
        tmp_path, monkeypatch,
        tracker_postings=[],  # tracker is empty
    )
    mgr.queue_path.write_text(json.dumps([
        {"opportunity_id": 99, "status": "queued",
         "retry_count": 0, "queued_at": "2026-05-05T00:00:00Z"},
    ]), encoding="utf-8")
    inv = mgr.inventory()
    assert inv["work"]["retry_count"] == 0
    # ghost retry should have been pruned from the on-disk queue too
    queue = json.loads(mgr.queue_path.read_text(encoding="utf-8"))
    assert queue == []


def test_inventory_clamps_negative_available(tmp_path, monkeypatch):
    mgr, *_ = _build_manager(
        tmp_path, monkeypatch, used_today=2000,
    )
    inv = mgr.inventory()
    assert inv["budget"]["available"] == 0


# --- PLAN ---------------------------------------------------------

def test_plan_full_run_under_budget(tmp_path, monkeypatch):
    mgr, *_ = _build_manager(
        tmp_path, monkeypatch,
        new_postings=[_posting(i) for i in range(10)],
    )
    p = mgr.plan(mgr.inventory())
    assert p["scenario"] == "FULL_RUN"
    assert p["eval_count"] == 10
    assert p["deferred_new"] == 0


def test_plan_partial_when_demand_exceeds_budget(tmp_path, monkeypatch):
    mgr, *_ = _build_manager(
        tmp_path, monkeypatch,
        new_postings=[_posting(i) for i in range(20)],
        used_today=CallManager.SOFT_STOP - 5,
    )
    p = mgr.plan(mgr.inventory())
    assert p["scenario"] == "PARTIAL_RUN"
    assert p["eval_count"] == 5
    assert p["deferred_new"] == 15


def test_plan_no_budget(tmp_path, monkeypatch):
    mgr, *_ = _build_manager(
        tmp_path, monkeypatch,
        new_postings=[_posting(i) for i in range(3)],
        used_today=CallManager.SOFT_STOP,
    )
    p = mgr.plan(mgr.inventory())
    assert p["scenario"] == "NO_BUDGET"
    assert p["eval_count"] == 0
    assert p["deferred_new"] == 3


def test_plan_newest_postings_first(tmp_path, monkeypatch):
    postings = [
        _posting(1, "2026-05-01T00:00:00Z"),
        _posting(2, "2026-05-07T00:00:00Z"),
        _posting(3, "2026-05-04T00:00:00Z"),
    ]
    mgr, *_ = _build_manager(
        tmp_path, monkeypatch, new_postings=postings,
        used_today=CallManager.SOFT_STOP - 2,
    )
    p = mgr.plan(mgr.inventory())
    assert [item["id"] for item in p["eval_batch"]] == [2, 3]


def test_plan_oldest_retries_first(tmp_path, monkeypatch):
    mgr, *_ = _build_manager(
        tmp_path, monkeypatch,
        tracker_postings=[_posting(10), _posting(11), _posting(12)],
        used_today=CallManager.SOFT_STOP - 2,
    )
    mgr.queue_path.write_text(json.dumps([
        {"opportunity_id": 10, "status": "queued",
         "retry_count": 0, "queued_at": "2026-05-05T00:00:00Z"},
        {"opportunity_id": 11, "status": "queued",
         "retry_count": 0, "queued_at": "2026-05-01T00:00:00Z"},
        {"opportunity_id": 12, "status": "queued",
         "retry_count": 0, "queued_at": "2026-05-03T00:00:00Z"},
    ]), encoding="utf-8")
    p = mgr.plan(mgr.inventory())
    assert [item["id"] for item in p["eval_batch"]] == [11, 12]


def test_plan_new_takes_budget_before_retries(tmp_path, monkeypatch):
    mgr, *_ = _build_manager(
        tmp_path, monkeypatch,
        new_postings=[_posting(i) for i in range(3)],
        tracker_postings=[_posting(99)],
        used_today=CallManager.SOFT_STOP - 2,
    )
    mgr.queue_path.write_text(json.dumps([
        {"opportunity_id": 99, "status": "queued",
         "retry_count": 0, "queued_at": "2026-04-01T00:00:00Z"},
    ]), encoding="utf-8")
    p = mgr.plan(mgr.inventory())
    # Available=2, both slots taken by NEW postings (newest first),
    # retry deferred to tomorrow.
    assert len(p["eval_batch"]) == 2
    assert all(item["id"] != 99 for item in p["eval_batch"])
    assert p["deferred_retries"] == 1


# --- EXECUTE ------------------------------------------------------

def test_execute_persists_each_proceed(tmp_path, monkeypatch):
    mgr, client, persist, *_ = _build_manager(
        tmp_path, monkeypatch,
        new_postings=[_posting(1), _posting(2)],
    )
    client.evaluate_posting.side_effect = [
        {"verdict": "PROCEED",
         "scores": {"function": 3, "domain": 2, "seniority": 3,
                    "disqualifier": False}},
        {"verdict": "SKIP", "skip_reason": "OTHER", "scores": None},
    ]
    p = mgr.plan(mgr.inventory())
    r = mgr.execute(p, auto=True)
    assert r["evaluated"] == 2
    assert r["proceeded"] == 1
    assert r["skipped_by_model"] == 1
    assert persist.call_count == 2


def test_execute_stops_at_soft_stop(tmp_path, monkeypatch):
    mgr, client, persist, *_ = _build_manager(
        tmp_path, monkeypatch,
        new_postings=[_posting(i) for i in range(3)],
    )
    counts = iter([
        0,                           # inventory()
        0, 0,                        # before/after item 0
        CallManager.SOFT_STOP,       # before item 1: stop
    ] + [CallManager.SOFT_STOP] * 5)  # padding
    client.count_today = MagicMock(side_effect=lambda: next(counts))
    client.evaluate_posting.return_value = {
        "verdict": "SKIP", "skip_reason": "OTHER", "scores": None,
    }
    p = mgr.plan(mgr.inventory())
    r = mgr.execute(p, auto=True)
    assert r["evaluated"] < 3


def test_execute_queues_failed_posting(tmp_path, monkeypatch):
    mgr, client, persist, *_ = _build_manager(
        tmp_path, monkeypatch,
        new_postings=[_posting(1)],
    )
    client.evaluate_posting.side_effect = ServerError("500 boom")
    p = mgr.plan(mgr.inventory())
    r = mgr.execute(p, auto=True)
    assert r["failed"] == 1
    persist.assert_not_called()
    queue = json.loads(mgr.queue_path.read_text(encoding="utf-8"))
    assert queue[0]["opportunity_id"] == 1
    assert queue[0]["retry_count"] == 0
    assert queue[0]["status"] == "queued"


def test_execute_retries_429_with_backoff(tmp_path, monkeypatch):
    mgr, client, *_ = _build_manager(
        tmp_path, monkeypatch,
        new_postings=[_posting(1)],
    )
    client.evaluate_posting.side_effect = [
        RateLimitedError("429"),
        RateLimitedError("429"),
        {"verdict": "SKIP", "skip_reason": "OTHER", "scores": None},
    ]
    sleeps = []
    monkeypatch.setattr(cm_mod.time, "sleep", lambda s: sleeps.append(s))
    p = mgr.plan(mgr.inventory())
    r = mgr.execute(p, auto=True)
    assert r["evaluated"] == 1
    assert client.evaluate_posting.call_count == 3
    assert sleeps == [2, 4]


def test_execute_does_not_retry_500(tmp_path, monkeypatch):
    mgr, client, *_ = _build_manager(
        tmp_path, monkeypatch,
        new_postings=[_posting(1)],
    )
    client.evaluate_posting.side_effect = ServerError("500 boom")
    p = mgr.plan(mgr.inventory())
    mgr.execute(p, auto=True)
    assert client.evaluate_posting.call_count == 1


# --- retry queue management --------------------------------------

def test_retry_abandoned_after_max_retries(tmp_path, monkeypatch):
    # Posting lives in tracker so the retry materializes; it is NOT
    # also in new_postings, so the retry is the only batch item.
    mgr, client, *_ = _build_manager(
        tmp_path, monkeypatch,
        tracker_postings=[_posting(1)],
    )
    mgr.queue_path.write_text(json.dumps([{
        "opportunity_id": 1, "status": "queued",
        "retry_count": CallManager.MAX_RETRIES_PER_POSTING - 1,
        "queued_at": "2026-05-01T00:00:00Z",
        "last_attempt_at": "2026-05-06T00:00:00Z",
    }]), encoding="utf-8")
    client.evaluate_posting.side_effect = ServerError("500 boom")
    p = mgr.plan(mgr.inventory())
    mgr.execute(p, auto=True)
    queue = json.loads(mgr.queue_path.read_text(encoding="utf-8"))
    assert queue[0]["status"] == "abandoned"
    assert queue[0]["retry_count"] == CallManager.MAX_RETRIES_PER_POSTING


def test_retry_completed_removed_from_queue(tmp_path, monkeypatch):
    mgr, client, *_ = _build_manager(
        tmp_path, monkeypatch,
        tracker_postings=[_posting(1)],
    )
    mgr.queue_path.write_text(json.dumps([{
        "opportunity_id": 1, "status": "queued",
        "retry_count": 0,
        "queued_at": "2026-05-01T00:00:00Z",
        "last_attempt_at": "2026-05-01T00:00:00Z",
        "employer": "X", "title": "Y",
    }]), encoding="utf-8")
    client.evaluate_posting.return_value = {
        "verdict": "SKIP", "skip_reason": "OTHER", "scores": None,
    }
    p = mgr.plan(mgr.inventory())
    mgr.execute(p, auto=True)
    queue = json.loads(mgr.queue_path.read_text(encoding="utf-8"))
    assert queue == []


def test_report_shows_counts(tmp_path, monkeypatch):
    mgr, *_ = _build_manager(
        tmp_path, monkeypatch, used_today=42,
    )
    r = mgr.report(
        {"evaluated": 100, "proceeded": 60,
         "skipped_by_model": 40, "failed": 0},
        {"deferred_new": 5, "deferred_retries": 1},
    )
    assert "Evaluated:          100" in r
    assert "Proceeded:        60" in r
    assert "42" in r
    assert "5 new + 1 retry" in r
