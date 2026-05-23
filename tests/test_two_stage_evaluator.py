"""Tests for the two-stage evaluator routing in cloud_pipeline.

Covers:
  * AI postings go DIRECT to cloud; cloud 200 -> CLOUD_EVALUATOR_VERSION
  * AI postings on cloud 500/503 fall back to local (FALLBACK_VERSION,
    NOT counted as a CallManager failure)
  * AI postings on cloud 4xx do NOT fall back (legacy queue path)
  * Non-AI postings: Stage 2pre SKIP -> PREFILTER_ONLY_VERSION
    (cloud not called)
  * Non-AI postings: Stage 2pre PROCEED -> cloud 200 -> CLOUD version
  * Non-AI postings: Stage 2pre PROCEED -> cloud 500 -> fallback
  * Idempotency: postings already at any of the four current versions
    are NOT re-picked by the planner.
  * CallManager 500 with on_cloud_5xx callback does NOT increment
    failed and does NOT enqueue.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.discovery.base import OpportunityRecord  # noqa: E402
from engine.llm.gemma_cloud_client import (  # noqa: E402
    GemmaCloudError, ServerError,
)
from engine.persistence.opportunities import persist_record  # noqa: E402
from engine.persistence.tracker import Tracker  # noqa: E402
from skills.role_evaluator import cloud_pipeline as cp  # noqa: E402
from skills.role_evaluator.pipeline import PipelineVerdict  # noqa: E402
from scripts.run_daily import find_postings_needing_eval  # noqa: E402
from scripts.run_full_eval_v2_3 import (  # noqa: E402
    CLOUD_EVALUATOR_VERSION,
    EVALUATOR_VERSION,
    FALLBACK_VERSION,
    PREFILTER_ONLY_VERSION,
)


# --- fixtures -----------------------------------------------------

@pytest.fixture
def tracker(tmp_path):
    t = Tracker(profile_id="p", db_path=tmp_path / "t.db")
    yield t
    t.close()


def _insert(tracker, *, title, location="Toronto, ON", url):
    """Insert via persist_record so v2.11 classification stamps run."""
    rec = OpportunityRecord(
        source="manual_entry",
        source_url=url,
        employer="Acme",
        title=title,
        location=location,
        posting_text="x",
        date_discovered=datetime(2026, 5, 9, tzinfo=timezone.utc),
    )
    opp_id, _ = persist_record(tracker, rec)
    return opp_id


class _PreResult:
    def __init__(self, verdict, skip_reason=None, latency_ms=10):
        self.verdict = verdict
        self.skip_reason = skip_reason
        self.latency_ms = latency_ms
        self.raw_response = ""
        self.prompt_eval_duration_ms = 0
        self.eval_duration_ms = 0


def _ok(verdict="PROCEED", **scores):
    return {
        "verdict": verdict,
        "scores": {
            "function": scores.get("function", 3),
            "domain": scores.get("domain", 3),
            "seniority": scores.get("seniority", 2),
            "disqualifier": scores.get("disqualifier", False),
        },
        "skip_reason": None,
    }


def _patch_run_batch_cloud(
    monkeypatch, tmp_path, *,
    cloud_responses,
    prefilter_results=None,
    fallback_verdicts=None,
):
    """Wire stubs into cloud_pipeline so run_batch_cloud uses them.

    cloud_responses: list of either parsed-dict (success) or
        Exception instances (raise on call N).
    prefilter_results: dict[posting_id] -> _PreResult.
        Default for missing keys: PROCEED.
    fallback_verdicts: dict[posting_id] -> PipelineVerdict.
        Default: EXPLORATORY/no-scores verdict.
    """
    monkeypatch.chdir(tmp_path)
    cloud_iter = iter(cloud_responses)

    class _StubClient:
        MODEL = "stub"

        def __init__(self, profile_id="p"):
            self._used = 0

        def count_today(self):
            return self._used

        def evaluate_posting(self, **kwargs):
            self._used += 1
            r = next(cloud_iter)
            if isinstance(r, Exception):
                raise r
            return r

    monkeypatch.setattr(cp, "GemmaCloudClient", _StubClient)

    pre_results = prefilter_results or {}

    def _stub_stage_2pre(posting, *_args, **_kwargs):
        return pre_results.get(posting["id"], _PreResult("PROCEED"))

    import skills.role_evaluator.stage2pre as s2pre_mod
    monkeypatch.setattr(s2pre_mod, "stage_2pre", _stub_stage_2pre)

    fb = fallback_verdicts or {}
    import skills.role_evaluator.pipeline as pipeline_mod

    def _stub_evaluate_posting(posting, *args, **kwargs):
        if posting["id"] in fb:
            return fb[posting["id"]]
        return PipelineVerdict(
            tier="EXPLORATORY",
            skip_at=None, skip_reason=None,
            pre_result=None, score_result=None, counter_result=None,
            combined=None,
            latency_total_ms=0,
            latency_breakdown={
                "2a_ms": 0, "2pre_ms": 0,
                "2c_score_ms": 0, "2c_counter_ms": 0,
            },
        )

    monkeypatch.setattr(
        pipeline_mod, "evaluate_posting", _stub_evaluate_posting,
    )
    monkeypatch.setattr(
        pipeline_mod, "_unload_model", lambda host, model: None,
    )

    import skills.role_evaluator.stage2a as s2a_mod

    class _AlwaysPass:
        def __init__(self, *_args, **_kwargs):
            pass

        def evaluate(self, posting):
            v = MagicMock()
            v.result = s2a_mod.Stage2aResult.PASS_TO_2B
            v.rule_fired = "passed_all_rules"
            return v

    monkeypatch.setattr(cp, "Stage2a", _AlwaysPass)

    import llm.client as llm_client_mod

    class _StubLLM:
        host = "http://stub"

        def __init__(self, model=None):
            self.model = model

    monkeypatch.setattr(llm_client_mod, "LLMClient", _StubLLM)

    monkeypatch.setattr(cp, "_load_prompt_template", lambda: "P")
    monkeypatch.setattr(
        cp, "_load_compact_inventory", lambda pid, py: "INV",
    )
    monkeypatch.setattr(cp, "_load_profile_yaml", lambda pid: {})
    monkeypatch.setattr(cp, "InventoryTool", lambda pid: MagicMock())
    monkeypatch.setattr(cp, "ProfileConfig", lambda pid: MagicMock())


def _latest_version(tracker, opp_id):
    row = tracker._query_one(
        "SELECT evaluator_version FROM eval_decisions "
        "WHERE opportunity_id = ? ORDER BY evaluated_at DESC LIMIT 1",
        (opp_id,),
    )
    return row["evaluator_version"] if row else None


# --- AI direct-to-cloud paths --------------------------------------

def test_ai_posting_cloud_200_persists_with_cloud_version(
    tracker, monkeypatch, tmp_path,
):
    pid = _insert(tracker, title="AI Engineer", url="https://x/1")
    _patch_run_batch_cloud(
        monkeypatch, tmp_path,
        cloud_responses=[_ok("PROCEED")],
    )
    cp.run_batch_cloud(tracker, profile_id="p", auto=True)
    assert _latest_version(tracker, pid) == CLOUD_EVALUATOR_VERSION


def test_ai_posting_cloud_500_falls_back_to_local(
    tracker, monkeypatch, tmp_path,
):
    pid = _insert(tracker, title="AI Engineer", url="https://x/1")
    _patch_run_batch_cloud(
        monkeypatch, tmp_path,
        cloud_responses=[ServerError("500 boom")],
    )
    cp.run_batch_cloud(tracker, profile_id="p", auto=True)
    assert _latest_version(tracker, pid) == FALLBACK_VERSION


def test_ai_posting_cloud_503_falls_back_to_local(
    tracker, monkeypatch, tmp_path,
):
    pid = _insert(tracker, title="AI Engineer", url="https://x/1")
    _patch_run_batch_cloud(
        monkeypatch, tmp_path,
        cloud_responses=[ServerError("503 boom")],
    )
    cp.run_batch_cloud(tracker, profile_id="p", auto=True)
    assert _latest_version(tracker, pid) == FALLBACK_VERSION


def test_ai_posting_cloud_4xx_does_not_fall_back(
    tracker, monkeypatch, tmp_path,
):
    pid = _insert(tracker, title="AI Engineer", url="https://x/1")
    _patch_run_batch_cloud(
        monkeypatch, tmp_path,
        cloud_responses=[GemmaCloudError("400 bad request")],
    )
    cp.run_batch_cloud(tracker, profile_id="p", auto=True)
    # Failed -> queued for retry. No persisted eval_decision yet.
    assert _latest_version(tracker, pid) is None


# --- Non-AI: stage 2pre routing -----------------------------------

def test_non_ai_prefilter_skip_persists_prefilter_version(
    tracker, monkeypatch, tmp_path,
):
    pid = _insert(
        tracker, title="Senior Project Manager", url="https://x/1",
    )
    _patch_run_batch_cloud(
        monkeypatch, tmp_path,
        cloud_responses=[],  # cloud should NOT be called
        prefilter_results={
            pid: _PreResult("SKIP", skip_reason="NO_FUNCTIONAL_OVERLAP"),
        },
    )
    cp.run_batch_cloud(tracker, profile_id="p", auto=True)
    assert _latest_version(tracker, pid) == PREFILTER_ONLY_VERSION


def test_non_ai_prefilter_proceed_then_cloud_200(
    tracker, monkeypatch, tmp_path,
):
    pid = _insert(
        tracker, title="Senior Project Manager", url="https://x/1",
    )
    _patch_run_batch_cloud(
        monkeypatch, tmp_path,
        cloud_responses=[_ok("PROCEED")],
    )
    cp.run_batch_cloud(tracker, profile_id="p", auto=True)
    assert _latest_version(tracker, pid) == CLOUD_EVALUATOR_VERSION


def test_non_ai_prefilter_proceed_cloud_500_falls_back(
    tracker, monkeypatch, tmp_path,
):
    pid = _insert(
        tracker, title="Senior Project Manager", url="https://x/1",
    )
    _patch_run_batch_cloud(
        monkeypatch, tmp_path,
        cloud_responses=[ServerError("500 boom")],
    )
    cp.run_batch_cloud(tracker, profile_id="p", auto=True)
    assert _latest_version(tracker, pid) == FALLBACK_VERSION


# --- Idempotency / planner re-pick guard --------------------------

def test_planner_does_not_repick_after_fallback(
    tracker, monkeypatch, tmp_path,
):
    pid = _insert(tracker, title="AI Engineer", url="https://x/1")
    _patch_run_batch_cloud(
        monkeypatch, tmp_path,
        cloud_responses=[ServerError("500")],
    )
    cp.run_batch_cloud(tracker, profile_id="p", auto=True)
    assert _latest_version(tracker, pid) == FALLBACK_VERSION

    candidates = find_postings_needing_eval(tracker)
    assert pid not in [p["id"] for p in candidates]


def test_planner_does_not_repick_after_prefilter_skip(
    tracker, monkeypatch, tmp_path,
):
    pid = _insert(
        tracker, title="Senior Project Manager", url="https://x/1",
    )
    _patch_run_batch_cloud(
        monkeypatch, tmp_path,
        cloud_responses=[],
        prefilter_results={
            pid: _PreResult("SKIP", skip_reason="NO_FUNCTIONAL_OVERLAP"),
        },
    )
    cp.run_batch_cloud(tracker, profile_id="p", auto=True)
    assert _latest_version(tracker, pid) == PREFILTER_ONLY_VERSION
    candidates = find_postings_needing_eval(tracker)
    assert pid not in [p["id"] for p in candidates]


# --- CallManager unit-level checks --------------------------------

def test_call_manager_500_with_callback_does_not_count_as_failed(
    tmp_path, monkeypatch,
):
    """ServerError routes through on_cloud_5xx and does NOT count as a
    failure. Regression guard for test_execute_queues_failed_posting:
    that test does NOT install the callback, so its semantics stay
    the same (queue + failed += 1)."""
    from engine.llm.call_manager import CallManager

    monkeypatch.chdir(tmp_path)
    client = MagicMock()
    client.count_today = MagicMock(return_value=0)
    client.evaluate_posting.side_effect = ServerError("500")
    invoked = []

    def _on_5xx(item, err):
        invoked.append((item["id"], type(err).__name__))

    mgr = CallManager(
        profile_id="t",
        gemma_client=client,
        find_postings_fn=lambda: [{
            "id": 1, "employer": "x", "title": "t",
            "location": "", "posting_text": "",
            "date_discovered": "2026-05-09T00:00:00Z",
        }],
        get_posting_fn=lambda pid: None,
        persist_fn=lambda i, p: None,
        prompt_template="P",
        inventory_summary="INV",
        on_cloud_5xx=_on_5xx,
    )
    plan = mgr.plan(mgr.inventory())
    r = mgr.execute(plan, auto=True)
    assert invoked == [(1, "ServerError")]
    assert r["fallback_to_local"] == 1
    assert r["failed"] == 0
    queue = (
        json.loads(mgr.queue_path.read_text(encoding="utf-8"))
        if mgr.queue_path.exists() else []
    )
    assert queue == []
