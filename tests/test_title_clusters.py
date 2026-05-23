"""Unit tests for engine/expansion/title_clusters.py (Spec D1 TASK 1).

Uses a fresh Tracker on tmp_path for each test rather than a hand-
rolled mock — keeps the IDF + query path realistic without touching
the live profile DB.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.expansion.title_clusters import (  # noqa: E402
    TitleClusterAnalyzer,
    _jaccard,
    _top_k_skill_ids,
)
from engine.persistence.tracker import Tracker  # noqa: E402


def _seed(
    tracker, *, title: str, employer: str,
    tier: str, fit_score: int,
    skill_ids: list[str] | None = None,
    evaluated_at: str | None = None,
) -> int:
    cid = tracker.upsert_company(name=employer)
    oid, _ = tracker.insert_opportunity(
        company_id=cid, source="test",
        source_url=f"https://example.test/{employer}/{title}/{fit_score}",
        title=title,
    )
    if skill_ids:
        tracker.update_opportunity_skills(oid, skill_ids)
    eid = tracker.record_evaluation(
        opportunity_id=oid,
        evaluator_version="pipeline-v2.3.0",
        tier=tier, fit_score=fit_score, sector=None,
        role_type=None, stage_trace={}, reasoning=None,
    )
    if evaluated_at is not None:
        tracker._execute(
            "UPDATE eval_decisions SET evaluated_at = ? WHERE id = ?",
            (evaluated_at, eid),
        )
    return oid


# --- Pure helpers --------------------------------------------------

def test_jaccard_similarity_calculation():
    assert _jaccard(set(), set()) == 0.0
    assert _jaccard({"a"}, set()) == 0.0
    assert _jaccard({"a", "b"}, {"a", "b"}) == 1.0
    # 1 shared of 3 unique => 1/3
    assert _jaccard({"a", "b"}, {"a", "c"}) == pytest.approx(1 / 3)
    # 2 shared of 4 unique => 0.5
    assert _jaccard({"a", "b", "c"}, {"a", "b", "d"}) == pytest.approx(0.5)


def test_top_k_picks_highest_idf_and_dedupes():
    idf = {"x": 5.0, "y": 4.0, "z": 3.0, "a": 1.0, "b": 0.5}
    # Duplicates collapsed, default_idf for missing IDs
    top3 = _top_k_skill_ids(
        ["x", "x", "y", "z", "a", "b"], idf, default_idf=0.0, k=3,
    )
    assert top3 == {"x", "y", "z"}


# --- Analyzer behaviour -------------------------------------------

def test_cluster_groups_similar_profiles(tmp_path):
    t = Tracker(profile_id="test_cluster_a", db_path=tmp_path / "t.db")
    try:
        # Three postings with overlapping top-5 skill profiles.
        common = ["s1", "s2", "s3", "s4", "s5"]
        for emp in ("AlphaCo", "BetaCo", "GammaCo"):
            _seed(t, title="Land Acquisition Officer", employer=emp,
                  tier="STRONG", fit_score=8, skill_ids=common)
        clusters = TitleClusterAnalyzer(t).analyze(days_back=30)
        assert len(clusters) == 1
        cl = clusters[0]
        assert cl.posting_count == 3
        assert cl.modal_title == "Land Acquisition Officer"
        assert set(cl.example_employers) == {"AlphaCo", "BetaCo", "GammaCo"}
    finally:
        t.close()


def test_cluster_ignores_skip_tier(tmp_path):
    t = Tracker(profile_id="test_cluster_skip", db_path=tmp_path / "t.db")
    try:
        common = ["s1", "s2", "s3", "s4", "s5"]
        for emp in ("AlphaCo", "BetaCo", "GammaCo"):
            _seed(t, title="Junk Officer", employer=emp,
                  tier="SKIP", fit_score=2, skill_ids=common)
        # A couple STRONG postings with totally different skills
        # should not be grouped with the skipped ones.
        for emp in ("DeltaCo", "EpsilonCo"):
            _seed(t, title="Different Role", employer=emp,
                  tier="STRONG", fit_score=7,
                  skill_ids=["q1", "q2", "q3", "q4", "q5"])
        clusters = TitleClusterAnalyzer(t).analyze(days_back=30)
        # Only 2 STRONG postings — below the size-3 minimum.
        assert clusters == []
    finally:
        t.close()


def test_cluster_requires_min_3_postings(tmp_path):
    t = Tracker(profile_id="test_cluster_min", db_path=tmp_path / "t.db")
    try:
        common = ["s1", "s2", "s3", "s4", "s5"]
        for emp in ("AlphaCo", "BetaCo"):  # only 2
            _seed(t, title="Land Acquisition Officer", employer=emp,
                  tier="STRONG", fit_score=8, skill_ids=common)
        clusters = TitleClusterAnalyzer(t).analyze(days_back=30)
        assert clusters == []
    finally:
        t.close()


def test_modal_title_selection(tmp_path):
    t = Tracker(profile_id="test_cluster_modal", db_path=tmp_path / "t.db")
    try:
        common = ["s1", "s2", "s3", "s4", "s5"]
        # Two postings titled "Project Coordinator", one titled
        # "Project Coordinator II" — modal should be the bare title.
        _seed(t, title="Project Coordinator", employer="A",
              tier="STRONG", fit_score=7, skill_ids=common)
        _seed(t, title="Project Coordinator", employer="B",
              tier="STRONG", fit_score=7, skill_ids=common)
        _seed(t, title="Project Coordinator II", employer="C",
              tier="STRONG", fit_score=7, skill_ids=common)
        clusters = TitleClusterAnalyzer(t).analyze(days_back=30)
        assert len(clusters) == 1
        assert clusters[0].modal_title == "Project Coordinator"
    finally:
        t.close()


def test_already_in_target_flag(tmp_path):
    t = Tracker(profile_id="test_cluster_target", db_path=tmp_path / "t.db")
    try:
        common = ["s1", "s2", "s3", "s4", "s5"]
        for emp in ("A", "B", "C"):
            _seed(t, title="Delivery Manager", employer=emp,
                  tier="STRONG", fit_score=8, skill_ids=common)
        for emp in ("D", "E", "F"):
            _seed(t, title="Land Acquisition Officer", employer=emp,
                  tier="STRONG", fit_score=8,
                  skill_ids=["x1", "x2", "x3", "x4", "x5"])
        clusters = TitleClusterAnalyzer(t).analyze(
            days_back=30,
            target_role_types=["delivery_manager", "scrum_master"],
        )
        by_title = {c.modal_title: c for c in clusters}
        assert by_title["Delivery Manager"].already_in_target is True
        assert by_title["Land Acquisition Officer"].already_in_target is False
    finally:
        t.close()


def test_empty_eval_decisions_returns_empty(tmp_path):
    t = Tracker(profile_id="test_cluster_empty", db_path=tmp_path / "t.db")
    try:
        assert TitleClusterAnalyzer(t).analyze(days_back=30) == []
    finally:
        t.close()


def test_days_back_filter(tmp_path):
    t = Tracker(profile_id="test_cluster_days", db_path=tmp_path / "t.db")
    try:
        common = ["s1", "s2", "s3", "s4", "s5"]
        old = (
            datetime.now(timezone.utc) - timedelta(days=60)
        ).isoformat()
        # Old postings outside the window
        for emp in ("A", "B", "C"):
            _seed(t, title="Old Role", employer=emp,
                  tier="STRONG", fit_score=8,
                  skill_ids=common, evaluated_at=old)
        # Recent postings inside the window
        for emp in ("D", "E", "F"):
            _seed(t, title="Fresh Role", employer=emp,
                  tier="STRONG", fit_score=8, skill_ids=common)
        clusters = TitleClusterAnalyzer(t).analyze(days_back=14)
        assert len(clusters) == 1
        assert clusters[0].modal_title == "Fresh Role"
    finally:
        t.close()
