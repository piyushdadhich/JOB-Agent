"""Unit tests for engine/expansion/adjacent_employers.py (Spec D1 TASK 3)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.expansion.adjacent_employers import (  # noqa: E402
    AdjacentEmployerFinder,
)
from engine.persistence.tracker import Tracker  # noqa: E402


def _seed(
    tracker, *, employer: str, title: str, tier: str,
    fit_score: int = 9,
    industry: str | None = None,
    city: str | None = None,
) -> int:
    cid = tracker.upsert_company(name=employer, industry=industry)
    oid, _ = tracker.insert_opportunity(
        company_id=cid, source="test",
        source_url=f"https://example.test/{employer}/{title}/{fit_score}",
        title=title,
    )
    if city is not None:
        tracker._execute(
            "UPDATE opportunities SET city = ? WHERE id = ?",
            (city, oid),
        )
    tracker.record_evaluation(
        opportunity_id=oid,
        evaluator_version="pipeline-v2.3.0",
        tier=tier, fit_score=fit_score, sector=None,
        role_type=None, stage_trace={}, reasoning=None,
    )
    return oid


def test_suggests_same_industry_companies(tmp_path):
    """A TOP_TIER employer's same-industry siblings in a target city
    surface as suggestions."""
    t = Tracker(profile_id="test_adj_a", db_path=tmp_path / "t.db")
    try:
        _seed(t, employer="TopBank", title="Senior PM", tier="TOP_TIER",
              industry="financial_services", city="toronto")
        # Same-industry, same-city candidate that is NOT yet strong.
        _seed(t, employer="OtherBank", title="Analyst", tier="EXPLORATORY",
              industry="financial_services", city="toronto")
        suggestions = AdjacentEmployerFinder(
            t, target_cities=["toronto", "calgary"],
        ).suggest(days_back=30)
        assert len(suggestions) == 1
        s = suggestions[0]
        assert s.source_company == "TopBank"
        assert s.suggested_company == "OtherBank"
        assert s.source_industry == "financial_services"
        assert s.suggested_industry == "financial_services"
    finally:
        t.close()


def test_excludes_already_scored_companies(tmp_path):
    """Companies that have any STRONG / TOP_TIER eval anywhere in
    history are NOT suggested as new candidates."""
    t = Tracker(profile_id="test_adj_b", db_path=tmp_path / "t.db")
    try:
        _seed(t, employer="TopBank", title="Senior PM", tier="TOP_TIER",
              industry="financial_services", city="toronto")
        # AlreadyStrong has a STRONG hit in the same industry — exclude.
        _seed(t, employer="AlreadyStrong", title="PM", tier="STRONG",
              industry="financial_services", city="toronto")
        # CandidateBank has only EXPLORATORY — include.
        _seed(t, employer="CandidateBank", title="Analyst",
              tier="EXPLORATORY",
              industry="financial_services", city="toronto")
        suggestions = AdjacentEmployerFinder(
            t, target_cities=["toronto"],
        ).suggest(days_back=30)
        names = {s.suggested_company for s in suggestions}
        assert names == {"CandidateBank"}
    finally:
        t.close()


def test_filters_by_target_cities(tmp_path):
    """Candidates whose only postings are outside target_cities are
    excluded."""
    t = Tracker(profile_id="test_adj_c", db_path=tmp_path / "t.db")
    try:
        _seed(t, employer="TopBank", title="Senior PM", tier="TOP_TIER",
              industry="financial_services", city="toronto")
        # WrongCity candidate has postings only in vancouver.
        _seed(t, employer="WrongCity", title="Analyst",
              tier="EXPLORATORY",
              industry="financial_services", city="vancouver")
        # RightCity candidate has postings in toronto.
        _seed(t, employer="RightCity", title="Analyst",
              tier="EXPLORATORY",
              industry="financial_services", city="toronto")
        suggestions = AdjacentEmployerFinder(
            t, target_cities=["toronto", "calgary"],
        ).suggest(days_back=30)
        names = {s.suggested_company for s in suggestions}
        assert names == {"RightCity"}
    finally:
        t.close()


def test_no_top_tier_returns_empty(tmp_path):
    """If no TOP_TIER employer exists in window, no suggestions."""
    t = Tracker(profile_id="test_adj_d", db_path=tmp_path / "t.db")
    try:
        _seed(t, employer="StrongOnly", title="PM", tier="STRONG",
              industry="financial_services", city="toronto")
        _seed(t, employer="OtherCo", title="A", tier="EXPLORATORY",
              industry="financial_services", city="toronto")
        suggestions = AdjacentEmployerFinder(
            t, target_cities=["toronto"],
        ).suggest(days_back=30)
        assert suggestions == []
    finally:
        t.close()


def test_deduplicates_suggestions(tmp_path):
    """A candidate with multiple postings in target cities is only
    emitted once per (source, candidate) pair."""
    t = Tracker(profile_id="test_adj_e", db_path=tmp_path / "t.db")
    try:
        _seed(t, employer="TopBank", title="PM", tier="TOP_TIER",
              industry="financial_services", city="toronto")
        # Candidate has three postings in two different target cities.
        _seed(t, employer="MultiCity", title="A", tier="EXPLORATORY",
              industry="financial_services", city="toronto")
        _seed(t, employer="MultiCity", title="B", tier="EXPLORATORY",
              industry="financial_services", city="toronto")
        _seed(t, employer="MultiCity", title="C", tier="EXPLORATORY",
              industry="financial_services", city="calgary")
        suggestions = AdjacentEmployerFinder(
            t, target_cities=["toronto", "calgary"],
        ).suggest(days_back=30)
        names = [s.suggested_company for s in suggestions]
        assert names.count("MultiCity") == 1
    finally:
        t.close()
