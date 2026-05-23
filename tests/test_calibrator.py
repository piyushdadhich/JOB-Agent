"""Spec 6 — calibrator pure-logic tests."""
from __future__ import annotations

import pytest

from engine.matching.calibrator import (
    ScorerCalibrator, Thresholds, distribution_from_rows,
)


# --- auto_calibrate: inventory-size seeding ---------------------

@pytest.mark.parametrize(
    "skill_count,expected",
    [
        (0,   Thresholds(top=3.0, strong=1.5, exploratory=0.5)),
        (49,  Thresholds(top=3.0, strong=1.5, exploratory=0.5)),
        (50,  Thresholds(top=4.0, strong=2.5, exploratory=1.0)),
        (100, Thresholds(top=4.0, strong=2.5, exploratory=1.0)),
        (149, Thresholds(top=4.0, strong=2.5, exploratory=1.0)),
        (150, Thresholds(top=5.0, strong=3.5, exploratory=1.5)),
        (500, Thresholds(top=5.0, strong=3.5, exploratory=1.5)),
    ],
)
def test_auto_calibrate_seeds_by_inventory_size(skill_count, expected):
    cal = ScorerCalibrator()
    # Neutral distribution (5-15% TOP_TIER → no adjustment).
    dist = {
        "total": 100, "top_tier_count": 10, "strong_count": 20,
        "exploratory_count": 30, "skip_count": 40,
    }
    assert cal.auto_calibrate(skill_count, dist) == expected


def test_auto_calibrate_tightens_on_too_many_top_tier():
    cal = ScorerCalibrator()
    dist = {
        "total": 100, "top_tier_count": 30,  # 30% — way over the cap
        "strong_count": 20, "exploratory_count": 30, "skip_count": 20,
    }
    t = cal.auto_calibrate(100, dist)
    # Medium seed (4.0/2.5/1.0) + 0.5 nudge up.
    assert t == Thresholds(top=4.5, strong=3.0, exploratory=1.5)


def test_auto_calibrate_loosens_on_too_few_top_tier():
    cal = ScorerCalibrator()
    dist = {
        "total": 100, "top_tier_count": 1,  # 1% — way under
        "strong_count": 5, "exploratory_count": 20, "skip_count": 74,
    }
    t = cal.auto_calibrate(100, dist)
    # Medium seed - 0.5 nudge.
    assert t == Thresholds(top=3.5, strong=2.0, exploratory=0.5)


def test_auto_calibrate_handles_zero_total():
    cal = ScorerCalibrator()
    # Fresh install — no scoring yet. Should fall back to the seed
    # without exploding on a divide-by-zero.
    t = cal.auto_calibrate(100, {"total": 0})
    # 0/1 = 0% top_tier → loosens.
    assert t.top == 3.5


# --- guided_tune ------------------------------------------------

def test_guided_tune_yes_yes_no_change():
    cal = ScorerCalibrator()
    cur = Thresholds(top=4.0, strong=2.5, exploratory=1.0)
    assert cal.guided_tune(
        cur, user_confirms_top=True, user_confirms_bottom=True,
    ) == cur


def test_guided_tune_no_top_raises():
    cal = ScorerCalibrator()
    cur = Thresholds(top=4.0, strong=2.5, exploratory=1.0)
    nxt = cal.guided_tune(
        cur, user_confirms_top=False, user_confirms_bottom=True,
    )
    assert nxt == Thresholds(top=4.5, strong=3.0, exploratory=1.5)


def test_guided_tune_no_bottom_lowers():
    cal = ScorerCalibrator()
    cur = Thresholds(top=4.0, strong=2.5, exploratory=1.0)
    nxt = cal.guided_tune(
        cur, user_confirms_top=True, user_confirms_bottom=False,
    )
    assert nxt == Thresholds(top=3.5, strong=2.0, exploratory=0.5)


def test_guided_tune_no_no_holds():
    # Conflicting feedback (both ends wrong) — no clear direction;
    # the calibrator holds rather than thrash.
    cal = ScorerCalibrator()
    cur = Thresholds(top=4.0, strong=2.5, exploratory=1.0)
    assert cal.guided_tune(
        cur, user_confirms_top=False, user_confirms_bottom=False,
    ) == cur


# --- Thresholds.clamped -----------------------------------------

def test_clamped_holds_minimum_gap():
    # User somehow nudged TOP below STRONG. Clamp re-imposes order.
    t = Thresholds(top=2.0, strong=3.0, exploratory=1.0)
    c = t.clamped()
    assert c.top == 2.0
    assert c.strong <= c.top - 0.5
    assert c.exploratory <= c.strong - 0.5


def test_clamped_holds_zero_floor():
    t = Thresholds(top=0.5, strong=-1.0, exploratory=-2.0)
    c = t.clamped()
    assert c.top == 0.5
    assert c.strong >= 0.0
    assert c.exploratory >= 0.0


def test_clamped_holds_ten_ceiling():
    t = Thresholds(top=12.0, strong=11.0, exploratory=10.5)
    c = t.clamped()
    assert c.top == 10.0
    assert c.strong <= 9.5


# --- distribution_from_rows --------------------------------------

def test_distribution_from_rows_counts_each_tier():
    rows = [
        {"tier": "TOP_TIER"},
        {"tier": "TOP_TIER"},
        {"tier": "STRONG"},
        {"tier": "EXPLORATORY"},
        {"tier": "SKIP"},
        {"tier": "SKIP"},
        {"tier": "SKIP"},
    ]
    d = distribution_from_rows(rows)
    assert d == {
        "top_tier_count": 2, "strong_count": 1,
        "exploratory_count": 1, "skip_count": 3,
        "total": 7,
    }


def test_distribution_from_rows_handles_lowercase_and_none():
    rows = [
        {"tier": "top_tier"},  # normalized to upper
        {"tier": None},        # ignored beyond bumping total
        {"tier": "WAT"},       # unknown tier still counts toward total
    ]
    d = distribution_from_rows(rows)
    assert d["total"] == 3
    assert d["top_tier_count"] == 1
    assert d["strong_count"] == 0
