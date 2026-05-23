"""Tests for the shared cloud budget counter."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine import cloud_budget  # noqa: E402
from engine.resume.cloud_generator import CloudResumeGenerator  # noqa: E402


def _write(log: Path, entries: list[dict]) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _today_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _yesterday_ts() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()


def test_calls_used_today_counts_correctly(tmp_path):
    log = tmp_path / "usage.jsonl"
    _write(log, [
        {"timestamp": _today_ts(), "call_type": "eval"},
        {"timestamp": _today_ts(), "call_type": "resume"},
        {"timestamp": _today_ts(), "call_type": "cover_letter"},
        {"timestamp": _yesterday_ts(), "call_type": "eval"},
        {"timestamp": _today_ts()},  # evaluator row, no call_type
    ])
    # 4 entries today (the yesterday one excluded).
    assert cloud_budget.calls_used_today("p", path=log) == 4


def test_calls_remaining_respects_limit(tmp_path):
    log = tmp_path / "usage.jsonl"
    _write(log, [{"timestamp": _today_ts()} for _ in range(10)])
    remaining = cloud_budget.calls_remaining_today("p", path=log)
    assert remaining == cloud_budget.DAILY_LIMIT - 10


def test_can_eval_false_when_exhausted(tmp_path):
    log = tmp_path / "usage.jsonl"
    _write(log, [
        {"timestamp": _today_ts()}
        for _ in range(cloud_budget.DAILY_LIMIT)
    ])
    assert cloud_budget.can_eval("p", path=log) is False
    # One fewer leaves headroom.
    _write(log, [
        {"timestamp": _today_ts()}
        for _ in range(cloud_budget.DAILY_LIMIT - 1)
    ])
    assert cloud_budget.can_eval("p", path=log) is True


def test_usage_breakdown_groups_by_call_type(tmp_path):
    log = tmp_path / "usage.jsonl"
    _write(log, [
        {"timestamp": _today_ts(), "call_type": "resume"},
        {"timestamp": _today_ts(), "call_type": "resume"},
        {"timestamp": _today_ts(), "call_type": "cover_letter"},
        {"timestamp": _today_ts()},  # no call_type → eval
    ])
    breakdown = cloud_budget.usage_breakdown_today("p", path=log)
    assert breakdown == {"resume": 2, "cover_letter": 1, "eval": 1}


def test_resume_generation_draws_from_shared_counter(tmp_path):
    """A resume generation logs to the same file the evaluator's
    budget check reads — so generation reduces eval headroom."""
    log = tmp_path / "usage.jsonl"

    class FakeClient:
        def generate(self, prompt, system=None, temperature=0.4):
            return "# Resume\n\nbody"

    gen = CloudResumeGenerator(profile_id="p", client=FakeClient())
    gen.usage_log = log
    assert cloud_budget.calls_used_today("p", path=log) == 0
    gen.generate("prompt one", call_type="resume")
    gen.generate("prompt two", call_type="cover_letter")
    # Both calls are visible to the shared counter.
    assert cloud_budget.calls_used_today("p", path=log) == 2
    assert cloud_budget.usage_breakdown_today("p", path=log) == {
        "resume": 1, "cover_letter": 1,
    }
