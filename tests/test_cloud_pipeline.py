"""Unit tests for skills.role_evaluator.cloud_pipeline.

Mocks the GemmaCloudClient and tracker — no live API or DB.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from skills.role_evaluator import cloud_pipeline as cp  # noqa: E402
from skills.role_evaluator.cloud_pipeline import (  # noqa: E402
    _persist_cloud_verdict,
    _persist_stage2a_skip,
    _scores_to_tier_and_fit,
    parsed_to_verdict,
)
from skills.role_evaluator.stage2a import Stage2aResult  # noqa: E402


def test_scores_to_tier_top_tier():
    tier, fit, norm = _scores_to_tier_and_fit({
        "function": 3, "domain": 3, "seniority": 3,
        "disqualifier": False,
    })
    assert tier == "TOP_TIER"
    assert fit == 10
    assert norm == 100.0


def test_scores_to_tier_strong():
    tier, _, _ = _scores_to_tier_and_fit({
        "function": 3, "domain": 1, "seniority": 2,
        "disqualifier": False,
    })
    # raw = 9+2+4 = 15; norm = 15/21*100 ~= 71.4 -> STRONG
    assert tier == "STRONG"


def test_scores_to_tier_disqualifier_halves_raw():
    no_disq = _scores_to_tier_and_fit({
        "function": 3, "domain": 3, "seniority": 3,
        "disqualifier": False,
    })
    with_disq = _scores_to_tier_and_fit({
        "function": 3, "domain": 3, "seniority": 3,
        "disqualifier": True,
    })
    assert with_disq[2] == pytest.approx(no_disq[2] / 2)


def test_parsed_to_verdict_proceed():
    v = parsed_to_verdict({
        "verdict": "PROCEED", "skip_reason": None,
        "scores": {
            "function": 3, "domain": 2, "seniority": 3,
            "disqualifier": False,
        },
    })
    assert v.tier in ("STRONG", "TOP_TIER")
    assert v.scores is not None
    assert v.skip_at is None


def test_parsed_to_verdict_skip():
    v = parsed_to_verdict({
        "verdict": "SKIP",
        "skip_reason": "NO_FUNCTIONAL_OVERLAP",
        "scores": None,
    })
    assert v.tier == "SKIP"
    assert v.skip_reason == "NO_FUNCTIONAL_OVERLAP"
    assert v.scores is None


def test_persist_cloud_verdict_calls_record_evaluation():
    tracker = MagicMock()
    parsed = {
        "verdict": "PROCEED", "skip_reason": None,
        "scores": {
            "function": 3, "domain": 2, "seniority": 3,
            "disqualifier": False,
        },
    }
    _persist_cloud_verdict(tracker, 42, parsed)
    tracker.record_evaluation.assert_called_once()
    kwargs = tracker.record_evaluation.call_args.kwargs
    assert kwargs["opportunity_id"] == 42
    assert kwargs["evaluator_version"] == "gemma4-cloud-v1"
    assert kwargs["tier"] in ("STRONG", "TOP_TIER")
    assert kwargs["fit_score"] is not None


def test_persist_stage2a_skip_writes_eval_and_stage_decision():
    tracker = MagicMock()
    s2a = MagicMock()
    s2a.rule_fired = "hard_exclusion:MegaBank"
    _persist_stage2a_skip(tracker, 7, s2a)
    tracker.record_evaluation.assert_called_once()
    rec = tracker.record_evaluation.call_args.kwargs
    assert rec["tier"] == "SKIP"
    assert "stage2a:hard_exclusion:MegaBank" in rec["reasoning"]
    tracker.insert_stage_decision.assert_called_once()


def test_evaluate_one_skips_at_stage2a():
    s2a = MagicMock()
    s2a.evaluate.return_value = MagicMock(
        result=Stage2aResult.REJECT_HARD,
        rule_fired="hard_exclusion:MegaBank",
    )
    client = MagicMock()
    posting = {
        "id": 1, "employer": "MegaBank", "title": "X",
        "location": "TO", "posting_text": "...",
    }
    v = cp.evaluate_one(
        posting, "P={posting_text}", "INV", client, s2a,
    )
    assert v.tier == "SKIP"
    assert v.skip_at == "2a"
    client.evaluate_posting.assert_not_called()


def test_evaluate_one_passes_through_to_cloud():
    s2a = MagicMock()
    s2a.evaluate.return_value = MagicMock(
        result=Stage2aResult.PASS_TO_2B,
    )
    client = MagicMock()
    client.evaluate_posting.return_value = {
        "verdict": "PROCEED", "skip_reason": None,
        "scores": {
            "function": 3, "domain": 2, "seniority": 3,
            "disqualifier": False,
        },
    }
    posting = {
        "id": 1, "employer": "Acme", "title": "Senior PM",
        "location": "Toronto", "posting_text": "Lead delivery.",
    }
    v = cp.evaluate_one(
        posting, "P={posting_text}", "INV", client, s2a,
    )
    assert v.tier in ("STRONG", "TOP_TIER")
    assert v.scores is not None
    client.evaluate_posting.assert_called_once()
