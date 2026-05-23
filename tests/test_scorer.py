"""Tests for engine.matching.scorer."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.matching.scorer import (
    MatchResult,
    compute_idf,
    load_inventory_skill_ids,
    score_opportunity,
)
from engine.persistence.tracker import Tracker


@pytest.fixture
def tmp_tracker(tmp_path):
    db = tmp_path / "tracker.db"
    t = Tracker(profile_id="test", db_path=db)
    yield t
    t.close()


def _seed_corpus(tracker, postings: list[list[str]]) -> list[int]:
    """Seed tracker with N postings; each posting gets the given
    skill_ids JSON-serialized into extracted_skill_ids. Returns
    list of opportunity_ids in order."""
    cid = tracker.upsert_company(name="Acme")
    ids = []
    for i, skill_ids in enumerate(postings):
        opp_id, _ = tracker.insert_opportunity(
            company_id=cid, source="test",
            source_url=f"https://x/{i}", title=f"role{i}",
        )
        tracker.update_opportunity_skills(opp_id, skill_ids)
        ids.append(opp_id)
    return ids


# ----- compute_idf --------------------------------------------------------

def test_compute_idf_returns_higher_for_rare_skills(tmp_tracker):
    # 5 postings; 'common' in 4, 'rare' in 1.
    _seed_corpus(tmp_tracker, [
        ["common", "rare"],
        ["common"],
        ["common"],
        ["common"],
        ["other"],
    ])
    idf = compute_idf(tmp_tracker)
    # common: log(5/4) = 0.223
    # rare: appears once -> capped at idf(min_df=2) = log(5/2) = 0.916
    assert idf["rare"] > idf["common"]


def test_compute_idf_caps_single_occurrence_skills(tmp_tracker):
    _seed_corpus(tmp_tracker, [
        ["a"], ["b"], ["c"],  # each appears once
        ["a", "b"],            # 'a' and 'b' now appear twice
    ])
    idf = compute_idf(tmp_tracker, min_df=2)
    # 'c' appears once -> capped at idf(2) = log(4/2) = log(2)
    expected_cap = math.log(4 / 2)
    assert idf["c"] == pytest.approx(expected_cap)


def test_compute_idf_common_skill_gets_low_idf(tmp_tracker):
    # All 4 postings contain 'ubiquitous'.
    _seed_corpus(tmp_tracker, [
        ["ubiquitous", "x"],
        ["ubiquitous", "y"],
        ["ubiquitous", "z"],
        ["ubiquitous", "w"],
    ])
    idf = compute_idf(tmp_tracker)
    # ubiquitous: log(4/4) = 0
    assert idf["ubiquitous"] == pytest.approx(0.0)


def test_compute_idf_empty_corpus(tmp_tracker):
    idf = compute_idf(tmp_tracker)
    assert idf == {}


# ----- load_inventory_skill_ids ------------------------------------------

def test_load_inventory_skill_ids_returns_set(tmp_tracker):
    tmp_tracker.upsert_profile_skills(
        profile_id="default", taxonomy="lightcast",
        skill_ids=["a", "b", "a"],  # duplicate to test dedup
        source_doc="x.md",
    )
    s = load_inventory_skill_ids(
        tmp_tracker, profile_id="default", taxonomy="lightcast",
    )
    assert s == {"a", "b"}


def test_load_inventory_skill_ids_missing_profile(tmp_tracker):
    s = load_inventory_skill_ids(
        tmp_tracker, profile_id="missing", taxonomy="lightcast",
    )
    assert s == set()


# ----- score_opportunity --------------------------------------------------

def test_score_opportunity_zero_overlap():
    r = score_opportunity(
        posting_skill_ids_raw=["a", "b", "c"],
        inventory_ids={"x", "y"},
        idf={"a": 1.0, "b": 1.0, "c": 1.0},
    )
    assert r.overlap_count == 0
    assert r.coverage_raw == 0.0
    assert r.coverage_idf == 0.0
    assert r.bucket == "zero"
    assert r.overlap_skill_ids == []
    assert sorted(r.missed_skill_ids) == ["a", "b", "c"]


def test_score_opportunity_full_overlap():
    r = score_opportunity(
        posting_skill_ids_raw=["a", "b", "c", "d"],
        inventory_ids={"a", "b", "c", "d", "e"},
        idf={s: 1.0 for s in "abcde"},
    )
    assert r.overlap_count == 4
    assert r.coverage_raw == 1.0
    assert r.coverage_idf == 1.0
    assert r.bucket == "high"


def test_score_opportunity_partial_overlap():
    r = score_opportunity(
        posting_skill_ids_raw=["a", "b", "c", "d"],
        inventory_ids={"a", "b", "x"},
        idf={s: 1.0 for s in "abcdx"},
    )
    assert r.overlap_count == 2
    assert r.coverage_raw == 0.5
    # uniform IDF -> coverage_idf == coverage_raw
    assert r.coverage_idf == pytest.approx(0.5)
    assert r.bucket == "low"
    assert sorted(r.overlap_skill_ids) == ["a", "b"]


def test_score_opportunity_deduplicates_posting_ids():
    # Posting has duplicates; should dedupe to 3 unique.
    r = score_opportunity(
        posting_skill_ids_raw=["a", "a", "b", "b", "c"],
        inventory_ids={"a", "b"},
        idf={"a": 1.0, "b": 1.0, "c": 1.0},
    )
    assert r.posting_skill_count == 3
    assert r.overlap_count == 2
    assert r.coverage_raw == pytest.approx(2 / 3)


def test_score_opportunity_idf_weighted_higher_for_rare_match():
    # Two scenarios with same raw overlap but different IDF
    # weights -- rare match should yield higher coverage_idf.
    common = score_opportunity(
        posting_skill_ids_raw=["common", "x", "y", "z"],
        inventory_ids={"common"},
        idf={"common": 0.1, "x": 0.1, "y": 0.1, "z": 0.1},
    )
    rare = score_opportunity(
        posting_skill_ids_raw=["rare", "x", "y", "z"],
        inventory_ids={"rare"},
        idf={"rare": 5.0, "x": 0.1, "y": 0.1, "z": 0.1},
    )
    # raw is the same (both 1/4)
    assert common.coverage_raw == rare.coverage_raw
    # but rare match weighs more heavily
    assert rare.coverage_idf > common.coverage_idf


def test_score_opportunity_bucket_zero():
    r = score_opportunity(["a"], {"x"}, {"a": 1.0, "x": 1.0})
    assert r.bucket == "zero"


def test_score_opportunity_bucket_low():
    # Overlap of 1, 2, 3 all map to 'low'.
    for n_overlap in (1, 2, 3):
        posting = [f"o{i}" for i in range(n_overlap)] + ["miss"]
        inventory = {f"o{i}" for i in range(n_overlap)}
        idf = {p: 1.0 for p in posting}
        r = score_opportunity(posting, inventory, idf)
        assert r.bucket == "low", f"overlap={n_overlap}"


def test_score_opportunity_bucket_high():
    # Overlap of 4 -> 'high'.
    posting = ["o1", "o2", "o3", "o4", "miss"]
    inventory = {"o1", "o2", "o3", "o4"}
    idf = {p: 1.0 for p in posting}
    r = score_opportunity(posting, inventory, idf)
    assert r.bucket == "high"


def test_score_opportunity_empty_posting():
    r = score_opportunity(
        posting_skill_ids_raw=[],
        inventory_ids={"a", "b"},
        idf={},
    )
    assert r.posting_skill_count == 0
    assert r.overlap_count == 0
    assert r.coverage_raw == 0.0
    assert r.coverage_idf == 0.0
    assert r.bucket == "zero"
