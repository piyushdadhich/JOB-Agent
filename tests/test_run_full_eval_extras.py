"""Spec JA-1-fix T1 — tests for the run_full_eval_v2_3 extras
wiring (interview_plan + culture_signals + letter_grade + red_flags
landing on eval_decisions rows for A/B grades).

The eval loop pulls these together through `_run_extras_for_verdict`.
Tests mock the verdict + tracker so they don't need a real LLM.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.persistence.tracker import Tracker  # noqa: E402
from scripts.run_full_eval_v2_3 import (  # noqa: E402
    _run_extras_for_verdict,
    persist_verdict,
)


# ---------------------------------------------------------------------------
# Verdict + combined stub
# ---------------------------------------------------------------------------

@dataclass
class _Combined:
    normalized: float
    raw: float = 0.0
    components: dict = None
    matched_skills: list = None
    gap_skills: list = None

    def __post_init__(self):
        if self.components is None:
            self.components = {}
        if self.matched_skills is None:
            self.matched_skills = ["python", "delivery", "scrum"]
        if self.gap_skills is None:
            self.gap_skills = ["go", "kubernetes"]


@dataclass
class _Verdict:
    tier: str
    combined: _Combined
    skip_at: str = None
    skip_reason: str = None
    pre_result = None
    score_result = None
    counter_result = None


@pytest.fixture
def tracker(tmp_path):
    t = Tracker(profile_id="x", db_path=tmp_path / "t.db")
    yield t
    t.close()


def _seed_posting(t: Tracker, *, fit_norm: float = 9.0) -> int:
    cid = t.upsert_company(name="Acme Bank", industry="fintech")
    oid, _ = t.insert_opportunity(
        company_id=cid, source="manual_entry",
        source_url="https://x/jx1",
        title="Director, Lending Platform",
        posting_text="Help us build a fast-paced platform.",
    )
    return oid


# ---------------------------------------------------------------------------
# A/B grade → interview_plan + culture_signals populated
# ---------------------------------------------------------------------------

_INTERVIEW_PLAN_FIXTURE = json.dumps([
    {
        "topic": "Delivery turnaround",
        "talking_point":
            "Led 18-person team and shipped 2 months ahead — maps "
            "directly to your platform timeline.",
        "proof_point": "Acme Corp ahead of contractual delivery",
    },
    {
        "topic": "API consolidation",
        "talking_point":
            "Consolidated 15 APIs into one decisioning service — "
            "useful for your unified-platform play.",
        "proof_point": "Discover decisioning API",
    },
    {
        "topic": "Quality discipline",
        "talking_point":
            "Zero defects at handoff via in-sprint testing — same "
            "pattern your CI/CD modernization needs.",
        "proof_point": "Acme Corp zero-defect handoff",
    },
])

_CULTURE_FIXTURE = json.dumps([
    {"signal": "DEI commitment", "sentiment": "positive"},
    {"signal": "Fast-paced", "sentiment": "negative"},
    {"signal": "Hybrid", "sentiment": "neutral"},
])


def _fake_ollama_router(prompt: str) -> str:
    """A/B-gate route: the interview-plan prompt mentions
    'interview talking points', the culture prompt mentions
    'culture signals'."""
    if "interview talking points" in prompt:
        return _INTERVIEW_PLAN_FIXTURE
    if "culture signals" in prompt:
        return _CULTURE_FIXTURE
    raise AssertionError(f"unexpected prompt: {prompt[:80]!r}")


def test_persist_verdict_populates_interview_plan_for_a_grade(
    tracker,
):
    """Norm 9.0 -> A. _run_extras_for_verdict + persist_verdict must
    land interview_plan and culture_signals on eval_decisions."""
    oid = _seed_posting(tracker)
    v = _Verdict(tier="TOP_TIER", combined=_Combined(normalized=9.0))
    extras = _run_extras_for_verdict(
        tracker, oid, v, ollama_call=_fake_ollama_router,
    )
    assert extras["letter_grade"] == "A"
    assert extras["interview_plan"], extras
    assert extras["culture_signals"], extras
    persist_verdict(
        tracker, oid, v,
        posting=extras["posting"], company=extras["company"],
        interview_plan=extras["interview_plan"],
        culture_signals=extras["culture_signals"],
    )
    row = tracker.get_latest_evaluation(oid)
    assert row["letter_grade"] == "A"
    plan = json.loads(row["interview_plan"])
    assert plan[0]["topic"] == "Delivery turnaround"
    signals = json.loads(row["culture_signals"])
    assert signals[0]["sentiment"] == "positive"


def test_persist_verdict_skips_interview_plan_for_d_grade(tracker):
    """Norm 3.5 -> D. _run_extras_for_verdict must NOT call Ollama
    for D-grade postings — caller passes a hot Ollama, but our
    A/B-gate keeps it cold."""
    oid = _seed_posting(tracker)
    v = _Verdict(tier="EXPLORATORY", combined=_Combined(normalized=3.5))

    def _boom(_prompt):
        raise AssertionError("Ollama should not be called for D grade")

    extras = _run_extras_for_verdict(
        tracker, oid, v, ollama_call=_boom,
    )
    assert extras["letter_grade"] == "D"
    assert extras["interview_plan"] is None
    assert extras["culture_signals"] is None


def test_extras_interview_plan_none_on_ollama_failure(tracker):
    """A-grade posting + Ollama call that raises => interview_plan
    collapses to None and the loop continues. culture_signals also
    None. Letter grade + red flags still computed."""
    oid = _seed_posting(tracker)
    v = _Verdict(tier="TOP_TIER", combined=_Combined(normalized=8.5))

    def _broken(_prompt):
        raise ConnectionError("ollama dead")

    extras = _run_extras_for_verdict(
        tracker, oid, v, ollama_call=_broken,
    )
    assert extras["letter_grade"] == "A"
    assert extras["interview_plan"] is None
    assert extras["culture_signals"] is None
    # red_flags is a list (possibly empty), not None — the helper
    # is deterministic and always returns.
    assert isinstance(extras["red_flags"], list)


def test_extras_letter_grade_b_also_runs(tracker):
    """B grade (norm 7.0) should still trigger the Gemma path."""
    oid = _seed_posting(tracker)
    v = _Verdict(tier="STRONG", combined=_Combined(normalized=7.0))
    extras = _run_extras_for_verdict(
        tracker, oid, v, ollama_call=_fake_ollama_router,
    )
    assert extras["letter_grade"] == "B"
    assert extras["interview_plan"]
    assert extras["culture_signals"]
