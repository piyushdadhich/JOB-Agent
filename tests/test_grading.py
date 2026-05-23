"""Spec JA-1 TASK 2 — tests for engine/evaluator/grading.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.evaluator.grading import (  # noqa: E402
    build_culture_signals,
    build_interview_plan,
    detect_red_flags,
    is_top_grade,
    score_to_letter,
)
from engine.persistence.tracker import Tracker  # noqa: E402


# ---------------------------------------------------------------------------
# score_to_letter
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "score, expected",
    [
        (10, "A"), (8.0, "A"), (8, "A"),
        (7.9, "B"), (6.5, "B"),
        (6.4, "C"), (5.0, "C"),
        (4.9, "D"), (3.0, "D"),
        (2.9, "F"), (0, "F"),
        (None, None),
    ],
)
def test_letter_grade_mapping(score, expected):
    assert score_to_letter(score) == expected


def test_is_top_grade():
    assert is_top_grade("A") is True
    assert is_top_grade("B") is True
    assert is_top_grade("C") is False
    assert is_top_grade("F") is False
    assert is_top_grade(None) is False


# ---------------------------------------------------------------------------
# Red flags
# ---------------------------------------------------------------------------

def test_red_flag_below_market():
    posting = {"salary_max": 60_000, "posting_text": ""}
    flags = detect_red_flags(posting)
    assert any(f["flag"] == "Below market compensation" for f in flags)


def test_red_flag_startup_signals():
    posting = {"posting_text": "We need someone who can wear many hats."}
    flags = detect_red_flags(posting)
    assert any(f["flag"] == "Startup workload signals" for f in flags)


def test_red_flag_unpaid_high_severity():
    posting = {"posting_text": "This is an unpaid internship."}
    flags = detect_red_flags(posting)
    unpaid = [f for f in flags if f["flag"] == "Unpaid position"]
    assert unpaid and unpaid[0]["severity"] == "high"


def test_red_flag_years_required():
    posting = {"posting_text": "We need 20+ years of experience."}
    flags = detect_red_flags(posting)
    assert any("20+ years required" in f["flag"] for f in flags)


def test_red_flag_tiny_company():
    posting = {"posting_text": ""}
    company = {"size_bucket": "tiny"}
    flags = detect_red_flags(posting, company)
    assert any(f["flag"] == "Company < 50 employees" for f in flags)


def test_red_flag_none_for_clean_posting():
    posting = {
        "salary_max": 150_000,
        "posting_text":
            "Senior Director of Delivery, full-time permanent role.",
    }
    flags = detect_red_flags(posting, company={"size_bucket": "mid"})
    assert flags == []


# ---------------------------------------------------------------------------
# Interview plan (A/B gated)
# ---------------------------------------------------------------------------

_INTERVIEW_PLAN_FIXTURE = json.dumps([
    {
        "topic": "Delivery turnaround",
        "talking_point":
            "Led 18-person team and shipped 2 months ahead — directly "
            "maps to your delivery-at-pace requirement.",
        "proof_point": "Acme Corp: ahead of contractual delivery",
    },
    {
        "topic": "API consolidation",
        "talking_point":
            "Consolidated 15 APIs into one — your unified platform vision "
            "aligns with that pattern.",
        "proof_point": "Discover decisioning API",
    },
    {
        "topic": "Quality discipline",
        "talking_point":
            "Zero defects at handoff via in-sprint testing — your CI/CD "
            "modernization needs the same discipline.",
        "proof_point": "Acme Corp: handoff with zero defects",
    },
])


def _fake_ollama_for_interview(prompt: str) -> str:
    assert "JSON" in prompt
    return _INTERVIEW_PLAN_FIXTURE


def test_interview_plan_generated_for_a_grade():
    plan = build_interview_plan(
        posting={"title": "Director of Delivery", "company_name": "Acme"},
        matched_skills=["delivery", "scrum", "java"],
        gap_skills=["aws", "kubernetes"],
        proof_points=[
            "Led 18-person cross-continent team",
            "Shipped 3 products in 7 months",
        ],
        ollama_call=_fake_ollama_for_interview,
    )
    assert len(plan) == 3
    assert all("talking_point" in item for item in plan)
    assert all(len(item["talking_point"]) > 10 for item in plan)


def test_no_interview_plan_for_d_grade():
    """Caller is responsible for gating on grade. Verify the
    grade-gating helper agrees: a D grade is NOT top, so the caller
    should skip the Gemma call entirely. build_interview_plan with
    no ollama_call returns []."""
    assert is_top_grade("D") is False
    plan = build_interview_plan(
        posting={"title": "Junior PM"},
        matched_skills=[], gap_skills=[], proof_points=[],
        ollama_call=None,
    )
    assert plan == []


def test_interview_plan_malformed_returns_empty():
    plan = build_interview_plan(
        posting={"title": "Lead Engineer"},
        matched_skills=["python"], gap_skills=["go"],
        proof_points=["one"],
        ollama_call=lambda _: "not json at all",
    )
    assert plan == []


# ---------------------------------------------------------------------------
# Culture signals
# ---------------------------------------------------------------------------

_CULTURE_FIXTURE = json.dumps([
    {"signal": "Strong DEI commitment", "sentiment": "positive"},
    {"signal": "Mentions 'fast-paced' twice", "sentiment": "negative"},
    {"signal": "Hybrid 3-day in-office", "sentiment": "neutral"},
])


def test_culture_signals_parsed():
    signals = build_culture_signals(
        posting={"posting_text": "Some posting text"},
        ollama_call=lambda _: _CULTURE_FIXTURE,
    )
    assert len(signals) == 3
    sentiments = {s["sentiment"] for s in signals}
    assert sentiments <= {"positive", "neutral", "negative"}


def test_culture_signals_invalid_sentiment_coerces_to_neutral():
    fixture = json.dumps([
        {"signal": "Mystery vibe", "sentiment": "spicy"},
    ])
    signals = build_culture_signals(
        posting={"posting_text": "x"},
        ollama_call=lambda _: fixture,
    )
    assert signals[0]["sentiment"] == "neutral"


# ---------------------------------------------------------------------------
# record_evaluation persists new columns
# ---------------------------------------------------------------------------

def test_record_evaluation_with_extras(tmp_path):
    db = tmp_path / "t.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        cid = t.upsert_company(name="Test Co")
        oid, _ = t.insert_opportunity(
            company_id=cid, source="manual",
            source_url="https://x/test/1", title="Director, Delivery",
        )
        red = [{"flag": "Below market", "severity": "medium"}]
        plan = [{
            "topic": "Quality", "talking_point": "tp",
            "proof_point": "pp",
        }]
        signals = [{"signal": "DEI", "sentiment": "positive"}]
        eval_id = t.record_evaluation(
            opportunity_id=oid,
            evaluator_version="pipeline-v2.3.0",
            tier="TOP_TIER", fit_score=9,
            sector=None, role_type=None,
            stage_trace={}, reasoning="ok",
            letter_grade="A",
            interview_plan=plan,
            red_flags=red,
            culture_signals=signals,
        )
        row = t.get_latest_evaluation(oid)
        assert row["letter_grade"] == "A"
        assert json.loads(row["interview_plan"])[0]["topic"] == "Quality"
        assert json.loads(row["red_flags"])[0]["flag"] == "Below market"
        assert json.loads(row["culture_signals"])[0]["signal"] == "DEI"
    finally:
        t.close()
