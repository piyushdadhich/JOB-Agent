"""Unit tests for skills.role_evaluator.score.combine.

Pure-Python deterministic combine; no LLM, no mocks needed beyond
constructing Stage2CScoreResult / Stage2CCounterResult instances
directly.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from skills.role_evaluator.score import (  # noqa: E402
    MAX_RAW,
    STRONG_DOWNGRADE_FLOOR,
    THRESHOLD_EXPLORATORY,
    THRESHOLD_STRONG,
    THRESHOLD_TOP_TIER,
    CombinedScore,
    combine,
)
from skills.role_evaluator.stage2c_counter import (  # noqa: E402
    Stage2CCounterResult,
)
from skills.role_evaluator.stage2c_score import (  # noqa: E402
    Stage2CScoreResult,
)


def _score(
    function: int = 0,
    domain: int = 0,
    seniority: int = 0,
    disqualifier: bool = False,
) -> Stage2CScoreResult:
    return Stage2CScoreResult(
        function_score=function,
        function_evidence="x",
        domain_score=domain,
        domain_evidence="x",
        seniority_score=seniority,
        seniority_evidence="x",
        disqualifier_present=disqualifier,
        disqualifier_reason="x" if disqualifier else None,
        raw_response="",
        latency_ms=0,
        prompt_eval_duration_ms=0,
        eval_duration_ms=0,
    )


def _counter(substantive: bool) -> Stage2CCounterResult:
    return Stage2CCounterResult(
        strongest_argument_against=(
            "The candidate lacks the required certification for this role"
            if substantive else "no_substantive_counter"
        ),
        counter_is_substantive=substantive,
        raw_response="",
        latency_ms=0,
        prompt_eval_duration_ms=0,
        eval_duration_ms=0,
    )


# --- Score math ----------------------------------------------------

def test_max_raw_is_21():
    assert MAX_RAW == 3 * 3 + 3 * 2 + 3 * 2


def test_perfect_scores_top_tier():
    result = combine(_score(3, 3, 3), None)
    assert result.raw == 21
    assert result.normalized == 100.0
    assert result.tier == "TOP_TIER"


def test_zero_scores_skip():
    result = combine(_score(0, 0, 0), None)
    assert result.raw == 0
    assert result.normalized == 0.0
    assert result.tier == "SKIP"


def test_function_weighted_3x():
    """function alone, score=1, should yield raw=3."""
    result = combine(_score(function=1), None)
    assert result.raw == 3


def test_domain_weighted_2x():
    result = combine(_score(domain=1), None)
    assert result.raw == 2


def test_seniority_weighted_2x():
    result = combine(_score(seniority=1), None)
    assert result.raw == 2


def test_normalized_in_0_100_range():
    """No matter what the inputs are within the 0-3 score range,
    normalized should land in [0, 100]."""
    for fn in range(4):
        for dom in range(4):
            for sen in range(4):
                for disq in (False, True):
                    result = combine(_score(fn, dom, sen, disq), None)
                    assert 0.0 <= result.normalized <= 100.0


# --- Disqualifier --------------------------------------------------

def test_disqualifier_halves_raw():
    """function=3, domain=3, seniority=3 with disqualifier:
    raw = 21 / 2 = 10.5 -> normalized = 50.0 -> EXPLORATORY."""
    result = combine(_score(3, 3, 3, disqualifier=True), None)
    assert result.raw == 10.5
    assert result.normalized == 50.0
    assert result.tier == "EXPLORATORY"


def test_disqualifier_on_partial_score():
    """function=2, domain=3, seniority=3 (raw=18) with disqualifier:
    raw = 9, normalized = 42.857 -> EXPLORATORY."""
    result = combine(_score(2, 3, 3, disqualifier=True), None)
    assert result.raw == 9
    assert 35 <= result.normalized < 60
    assert result.tier == "EXPLORATORY"


# --- Counter-argument downgrades -----------------------------------

def test_counter_downgrades_top_tier_to_strong():
    """Perfect score (TOP_TIER) with substantive counter -> STRONG."""
    result = combine(_score(3, 3, 3), _counter(substantive=True))
    assert result.tier == "STRONG"


def test_counter_downgrades_low_strong_to_exploratory():
    """function=3, domain=1, seniority=1 -> raw=13 -> 61.9% -> STRONG
    (below STRONG_DOWNGRADE_FLOOR=65). Substantive counter downgrades
    to EXPLORATORY because the score is in the borderline-STRONG band."""
    score = _score(3, 1, 1)
    no_counter = combine(score, None)
    assert no_counter.tier == "STRONG"
    assert 60 <= no_counter.normalized < STRONG_DOWNGRADE_FLOOR
    with_counter = combine(score, _counter(substantive=True))
    assert with_counter.tier == "EXPLORATORY"


def test_counter_does_not_downgrade_high_strong():
    """function=2, domain=3, seniority=2 -> raw=16 -> 76% -> STRONG
    (above STRONG_DOWNGRADE_FLOOR=65). Substantive counter does NOT
    downgrade because the score is robustly in the STRONG band -
    a domain-specific concern the candidate can plausibly bridge
    is not enough to move a clearly-strong match."""
    score = _score(2, 3, 2)
    base = combine(score, None)
    assert base.tier == "STRONG"
    assert base.normalized >= STRONG_DOWNGRADE_FLOOR
    with_counter = combine(score, _counter(substantive=True))
    assert with_counter.tier == "STRONG"


def test_counter_does_not_downgrade_strong_at_67_percent():
    """raw=14 (66.7%) is the empirical sweet spot from iter 3 fast
    eval: 5 known-good shortlists landed there. With the threshold
    at 65, substantive counter must NOT downgrade these."""
    score = _score(2, 2, 2)
    assert combine(score, None).normalized > STRONG_DOWNGRADE_FLOOR
    with_counter = combine(score, _counter(substantive=True))
    assert with_counter.tier == "STRONG"


def test_counter_does_not_affect_exploratory():
    """function=2, domain=1, seniority=1 -> raw=10 -> 47.6% -> EXPLORATORY.
    Substantive counter does NOT downgrade EXPLORATORY further."""
    score = _score(2, 1, 1)
    base = combine(score, None)
    assert base.tier == "EXPLORATORY"
    with_counter = combine(score, _counter(substantive=True))
    assert with_counter.tier == "EXPLORATORY"


def test_counter_does_not_affect_skip():
    """Zero scores -> SKIP. Substantive counter does NOT change."""
    score = _score(0, 0, 0)
    with_counter = combine(score, _counter(substantive=True))
    assert with_counter.tier == "SKIP"


def test_non_substantive_counter_does_not_downgrade():
    """If counter_is_substantive=False, tier should be unchanged."""
    score = _score(3, 3, 3)
    result = combine(score, _counter(substantive=False))
    assert result.tier == "TOP_TIER"


def test_no_counter_passed_does_not_downgrade():
    """counter_result=None -> no downgrade, components.counter_substantive=None."""
    result = combine(_score(3, 3, 3), None)
    assert result.tier == "TOP_TIER"
    assert result.components["counter_substantive"] is None


def test_disqualifier_and_counter_both_apply():
    """Perfect score (raw=21), disqualifier (raw=10.5, normalized=50%,
    EXPLORATORY), substantive counter does NOT further downgrade
    because we are already in EXPLORATORY."""
    result = combine(
        _score(3, 3, 3, disqualifier=True),
        _counter(substantive=True),
    )
    assert result.raw == 10.5
    assert result.tier == "EXPLORATORY"


def test_disqualifier_and_counter_borderline():
    """function=3, domain=3, seniority=3, no disq, raw=21 (TOP_TIER).
    With substantive counter: TOP_TIER -> STRONG. With disqualifier:
    raw=10.5 -> EXPLORATORY. Both: raw=10.5 -> EXPLORATORY (counter
    no-op because already EXPLORATORY)."""
    full = combine(_score(3, 3, 3), None)
    assert full.tier == "TOP_TIER"
    with_disq = combine(_score(3, 3, 3, disqualifier=True), None)
    assert with_disq.tier == "EXPLORATORY"
    with_counter = combine(_score(3, 3, 3), _counter(True))
    assert with_counter.tier == "STRONG"
    both = combine(_score(3, 3, 3, disqualifier=True), _counter(True))
    assert both.tier == "EXPLORATORY"


# --- Threshold consistency ----------------------------------------

def test_thresholds_consistent():
    """80 > 60 > 35 > 0; ordering matches tier severity."""
    assert THRESHOLD_TOP_TIER > THRESHOLD_STRONG > THRESHOLD_EXPLORATORY > 0


def test_threshold_boundary_top_tier_at_80():
    """Exactly 80% normalizes to TOP_TIER (>= boundary)."""
    # function=2, domain=3, seniority=3 -> raw=18 -> 18/21 = 85.7% -> TOP_TIER
    result = combine(_score(2, 3, 3), None)
    assert result.normalized > 80
    assert result.tier == "TOP_TIER"


def test_threshold_boundary_strong_at_60():
    """function=2, domain=2, seniority=2 -> raw=14 -> 66.7% -> STRONG."""
    result = combine(_score(2, 2, 2), None)
    assert 60 <= result.normalized < 80
    assert result.tier == "STRONG"


def test_threshold_boundary_skip_below_35():
    """function=1, domain=1, seniority=1 -> raw=7 -> 33.3% -> SKIP."""
    result = combine(_score(1, 1, 1), None)
    assert result.normalized < 35
    assert result.tier == "SKIP"


# --- Components recording ----------------------------------------

def test_components_recorded():
    result = combine(_score(2, 1, 3, disqualifier=True), _counter(True))
    assert result.components == {
        "function": 2,
        "domain": 1,
        "seniority": 3,
        "disqualifier": True,
        "counter_substantive": True,
    }


def test_components_counter_substantive_none_when_counter_skipped():
    result = combine(_score(2, 2, 2), None)
    assert result.components["counter_substantive"] is None


# --- Result type --------------------------------------------------

def test_returns_combined_score_dataclass():
    result = combine(_score(1, 1, 1), None)
    assert isinstance(result, CombinedScore)
    assert hasattr(result, "raw")
    assert hasattr(result, "normalized")
    assert hasattr(result, "tier")
    assert hasattr(result, "components")
