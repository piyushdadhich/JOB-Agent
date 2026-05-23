"""Tests for engine.matching.scorer.ThreeSignalScorer (Spec B2 TASK 4).

Coexists with the legacy two-signal score_opportunity tests in
test_scorer.py — this file only exercises the new class.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.matching.scorer import (  # noqa: E402
    ScoringResult,
    ThreeSignalScorer,
)


# ----- coverage signal ----------------------------------------------------

def test_perfect_coverage_returns_1():
    s = ThreeSignalScorer({"a", "b", "c"})
    r = s.score({"a", "b", "c"})
    assert r.coverage == 1.0


def test_zero_coverage_returns_0():
    s = ThreeSignalScorer({"a", "b", "c"})
    r = s.score({"x", "y"})
    assert r.coverage == 0.0


def test_partial_coverage():
    s = ThreeSignalScorer({"a", "b", "c"})
    r = s.score({"a", "x"})  # 1 of 2 posting skills matched
    assert r.coverage == 0.5


def test_empty_posting_skills_returns_0():
    s = ThreeSignalScorer({"a", "b"})
    r = s.score(set())
    assert r.coverage == 0.0
    assert r.rarity == 0.0
    assert r.bridge == 0.0
    assert r.overall == 0.0
    assert r.tier == "SKIP"


def test_empty_inventory_returns_0_overall():
    s = ThreeSignalScorer(set())
    r = s.score({"a", "b"})
    # No inventory => coverage 0, rarity 0 (stub == coverage),
    # bridge 0.5 (stub) -> overall = 0.5*0 + 0.35*0 + 0.15*0.5 = 0.075,
    # *10 = 0.75 -> SKIP tier.
    assert r.coverage == 0.0
    assert r.tier == "SKIP"


# ----- overall + tier --------------------------------------------------

def test_overall_score_formula_with_stub_rarity():
    """Overall = (0.5*cov + 0.35*rarity + 0.15*bridge) * 10.
    With rarity stubbed to coverage, perfect coverage gives:
      (0.5*1.0 + 0.35*1.0 + 0.15*0.5) * 10 = 9.25 -> TOP_TIER."""
    s = ThreeSignalScorer({"a", "b"})
    r = s.score({"a", "b"})
    assert abs(r.overall - 9.25) < 1e-9
    assert r.tier == "TOP_TIER"


def test_tier_top_tier_threshold():
    """A score >=4.0 is TOP_TIER (Spec B3 TASK 1 thresholds)."""
    # Construct: posting overlaps fully with inventory of size 5.
    s = ThreeSignalScorer({"a", "b", "c", "d", "e"})
    r = s.score({"a", "b", "c", "d", "e"})
    assert r.overall >= 4.0
    assert r.tier == "TOP_TIER"


def test_tier_strong_threshold():
    """A score in [2.5, 4.0) is STRONG (Spec B3 TASK 1 thresholds)."""
    # coverage 0.25: overall = (0.5*0.25 + 0.35*0.25 + 0.15*0.5)*10
    # = 1.25 + 0.875 + 0.75 = 2.875 -> STRONG under B3 thresholds.
    inv = {f"s{i}" for i in range(20)}
    posting = set(list(inv)[:5]) | {f"miss{i}" for i in range(15)}
    s = ThreeSignalScorer(inv)
    r = s.score(posting)
    assert 2.5 <= r.overall < 4.0
    assert r.tier == "STRONG"


def test_tier_exploratory_threshold():
    """A score in [1.0, 2.5) is EXPLORATORY (Spec B3 TASK 1 thresholds)."""
    # coverage 0.1: overall = (0.5*0.1 + 0.35*0.1 + 0.15*0.5)*10
    # = 0.5 + 0.35 + 0.75 = 1.6 -> EXPLORATORY under B3 thresholds.
    inv = {f"s{i}" for i in range(10)}
    posting = set(list(inv)[:1]) | {f"miss{i}" for i in range(9)}
    s = ThreeSignalScorer(inv)
    r = s.score(posting)
    assert 1.0 <= r.overall < 2.5
    assert r.tier == "EXPLORATORY"


def test_tier_skip_threshold():
    """A score <1.0 is SKIP (Spec B3 TASK 1 thresholds).
    Only essentially-zero-overlap postings should be SKIP."""
    # Zero coverage: overall = (0 + 0 + 0.15*0.5)*10 = 0.75 -> SKIP.
    inv = {"a", "b", "c"}
    posting = {"x", "y", "z"}  # nothing matches
    s = ThreeSignalScorer(inv)
    r = s.score(posting)
    assert r.overall < 1.0
    assert r.tier == "SKIP"


# ----- matched / missed sets ------------------------------------------

def test_matched_set_correct():
    s = ThreeSignalScorer({"a", "b", "c"})
    r = s.score({"a", "c", "x"})
    assert r.matched == {"a", "c"}


def test_missed_set_correct():
    s = ThreeSignalScorer({"a", "b", "c"})
    r = s.score({"a", "c", "x", "y"})
    assert r.missed == {"x", "y"}


# ----- rarity with real IDF ------------------------------------------

def test_rarity_with_idf_weights_matched_skills():
    """When IDF is supplied, rarity = sum(idf of matched) /
    sum(idf of posting). High-IDF matched skills boost the score
    over uniform-weight coverage."""
    inv = {"rare", "common"}
    posting = {"rare", "common", "noise"}
    idf = {"rare": 5.0, "common": 1.0, "noise": 1.0}
    s = ThreeSignalScorer(inv, idf=idf)
    r = s.score(posting)
    # matched IDF total = 5 + 1 = 6; posting IDF total = 5+1+1 = 7.
    assert abs(r.rarity - 6.0 / 7.0) < 1e-9
    # coverage = 2/3 = 0.667; rarity 0.857 > coverage => rare boost.
    assert r.rarity > r.coverage


def test_list_input_accepted():
    """score() accepts lists (legacy flat JSON shape) as well as sets."""
    s = ThreeSignalScorer({"a", "b"})
    r = s.score(["a", "b", "x"])  # list
    # set(["a","b","x"]) -> coverage = 2/3
    assert abs(r.coverage - 2.0 / 3.0) < 1e-9


# ----- result type sanity --------------------------------------------

def test_result_type_is_scoring_result():
    s = ThreeSignalScorer({"a"})
    r = s.score({"a"})
    assert isinstance(r, ScoringResult)
    assert r.posting_count == 1
    assert r.inventory_count == 1
