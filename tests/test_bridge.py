"""Tests for engine.matching.bridge (Spec B3 TASK 3)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.matching.bridge import (  # noqa: E402
    BridgeCalculator,
    load_lightcast_hierarchy,
)
from engine.matching.scorer import ThreeSignalScorer  # noqa: E402


# A small synthetic hierarchy:
#   IT category 17.0
#     - 17.0.442 subcategory
#     - 17.0.474 subcategory
#   Engineering category 11.0
#     - 11.0.100 subcategory
H = {
    "inv_a":  ["17.0", "17.0.442.0"],
    "inv_b":  ["11.0", "11.0.100.0"],
    "post_x": ["17.0", "17.0.442.0"],   # same subcat as inv_a
    "post_y": ["17.0", "17.0.474.0"],   # same cat as inv_a, diff subcat
    "post_z": ["20.0", "20.0.999.0"],   # different cat altogether
    "post_w": [],                         # empty hierarchy
}


# --- BridgeCalculator core --------------------------------------------

def test_same_subcategory_returns_075():
    calc = BridgeCalculator(H)
    # post_x and inv_a share both hierarchy levels.
    score = calc.bridge_score({"post_x"}, {"inv_a"})
    assert score == 0.75


def test_same_category_returns_050():
    calc = BridgeCalculator(H)
    # post_y and inv_a share cat 17.0 only.
    score = calc.bridge_score({"post_y"}, {"inv_a"})
    assert score == 0.50


def test_different_category_returns_025():
    calc = BridgeCalculator(H)
    # post_z is in cat 20.0; inv_a is in 17.0.
    score = calc.bridge_score({"post_z"}, {"inv_a"})
    assert score == 0.25


def test_perfect_match_returns_10():
    """When the entire posting is in the inventory, bridge is 1.0."""
    calc = BridgeCalculator(H)
    score = calc.bridge_score({"inv_a"}, {"inv_a"})
    assert score == 1.0


def test_empty_posting_returns_0():
    calc = BridgeCalculator(H)
    assert calc.bridge_score(set(), {"inv_a"}) == 0.0


def test_empty_inventory_returns_0():
    calc = BridgeCalculator(H)
    assert calc.bridge_score({"post_x"}, set()) == 0.0


def test_missed_skill_without_hierarchy_skipped():
    """post_w has no hierarchy. With inv_a in the inventory, the
    posting reduces to no countable missed skills -> 0.0."""
    calc = BridgeCalculator(H)
    score = calc.bridge_score({"post_w"}, {"inv_a"})
    assert score == 0.0


def test_average_across_multiple_missed_skills():
    """post_x (0.75) + post_z (0.25) -> avg 0.50."""
    calc = BridgeCalculator(H)
    score = calc.bridge_score({"post_x", "post_z"}, {"inv_a"})
    assert score == pytest.approx(0.50)


def test_picks_best_inventory_match():
    """For each missed skill, the calculator picks the closest
    inventory match. post_y is in cat 17.0 (close to inv_a) but
    different cat from inv_b. Best proximity = 0.50 via inv_a."""
    calc = BridgeCalculator(H)
    score = calc.bridge_score({"post_y"}, {"inv_a", "inv_b"})
    assert score == 0.50


# --- ThreeSignalScorer integration ------------------------------------

def test_scorer_uses_bridge_calculator_when_provided():
    """When BridgeCalculator is passed in, the bridge signal is
    computed from it (not the legacy 0.5 stub)."""
    calc = BridgeCalculator(H)
    s = ThreeSignalScorer(
        {"inv_a"}, idf=None, bridge_calculator=calc,
    )
    # post_x missed, same subcat -> bridge 0.75.
    r = s.score({"post_x"})
    assert r.bridge == 0.75
    # Different category -> bridge 0.25.
    r2 = s.score({"post_z"})
    assert r2.bridge == 0.25


def test_scorer_without_bridge_calculator_uses_stub():
    """Backwards compat: legacy callers get the 0.5 stub."""
    s = ThreeSignalScorer({"inv_a"})
    r = s.score({"post_x"})
    assert r.bridge == 0.5


# --- load_lightcast_hierarchy ----------------------------------------

def test_load_missing_file_returns_empty(tmp_path):
    out = load_lightcast_hierarchy(tmp_path / "nope.json")
    assert out == {}


def test_load_real_project_hierarchy_is_nonempty():
    """The committed data/lightcast_hierarchy.json must load and
    contain at least 10000 skill IDs (sanity check)."""
    out = load_lightcast_hierarchy()
    assert len(out) >= 10000
    # Every value should be a list of strings.
    for sid, levels in list(out.items())[:50]:
        assert isinstance(levels, list)
        assert all(isinstance(x, str) for x in levels)


def test_load_from_synthetic_file(tmp_path):
    path = tmp_path / "h.json"
    path.write_text(json.dumps({
        "KS-abc": ["17.0", "17.0.442.0"],
    }), encoding="utf-8")
    out = load_lightcast_hierarchy(path)
    assert out == {"KS-abc": ["17.0", "17.0.442.0"]}
