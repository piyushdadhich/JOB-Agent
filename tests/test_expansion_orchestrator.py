"""Unit tests for engine/expansion/orchestrator.py (Spec D1 TASK 5)."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.expansion.orchestrator import (  # noqa: E402
    ExpansionOrchestrator,
    ExpansionReport,
)
from engine.expansion.title_clusters import TitleCluster  # noqa: E402
from engine.persistence.tracker import Tracker  # noqa: E402


def _seed(
    tracker, *, employer: str, title: str, tier: str,
    skill_ids: list[str] | None = None,
    industry: str | None = None,
    city: str | None = None,
    fit_score: int = 8,
) -> int:
    cid = tracker.upsert_company(name=employer, industry=industry)
    oid, _ = tracker.insert_opportunity(
        company_id=cid, source="test",
        source_url=f"https://example.test/{employer}/{title}/{fit_score}",
        title=title,
    )
    if skill_ids:
        tracker.update_opportunity_skills(oid, skill_ids)
    if city is not None:
        tracker._execute(
            "UPDATE opportunities SET city = ? WHERE id = ?",
            (city, oid),
        )
    tracker.record_evaluation(
        opportunity_id=oid, evaluator_version="pipeline-v2.3.0",
        tier=tier, fit_score=fit_score, sector=None,
        role_type=None, stage_trace={}, reasoning=None,
    )
    return oid


def test_orchestrator_runs_all_strategies(tmp_path):
    """Run with seed data that triggers all four strategies."""
    t = Tracker(profile_id="test_orch_a", db_path=tmp_path / "t.db")
    try:
        common_skills = ["s1", "s2", "s3", "s4", "s5"]
        # Title cluster: three same-title postings with the same
        # skill profile — three distinct employers so the orchestrator
        # also clears go-deep's min_count.
        for i, emp in enumerate(("TopBank", "OtherTopBank", "ThirdBank")):
            _seed(t, employer=emp,
                  title="Land Acquisition Officer",
                  tier="TOP_TIER", skill_ids=common_skills,
                  industry="financial_services", city="toronto",
                  fit_score=8 + (i % 2))
        # Add an extra TOP_TIER posting at TopBank so it clears
        # min_count=2 on its own.
        _seed(t, employer="TopBank",
              title="Land Acquisition Specialist",
              tier="TOP_TIER", skill_ids=common_skills,
              industry="financial_services", city="toronto",
              fit_score=7)
        # Adjacent-only candidate (same industry, not yet strong)
        _seed(t, employer="OtherBank", title="Analyst",
              tier="EXPLORATORY",
              industry="financial_services", city="toronto")
        # EXPLORATORY postings with a recurring missed skill
        for emp in ("X", "Y", "Z"):
            _seed(t, employer=emp, title="Some Role",
                  tier="EXPLORATORY",
                  skill_ids=["gap_skill"])

        report = ExpansionOrchestrator(
            tracker=t,
            profile={
                "target_role_types": ["delivery_manager"],
                "target_cities": ["toronto", "calgary"],
            },
            inventory_skill_ids=set(common_skills),
        ).run(days_back=30)

        assert isinstance(report, ExpansionReport)
        assert report.days_analyzed == 30
        assert len(report.title_clusters) == 1
        assert report.title_clusters[0].modal_title == "Land Acquisition Officer"
        # TopBank has 3 TOP_TIER postings → qualifies for deep-target
        assert any(d.company_name == "TopBank" for d in report.deep_targets)
        # OtherBank should be an adjacent suggestion (same industry +
        # city, never STRONG/TOP_TIER)
        assert any(
            s.suggested_company == "OtherBank"
            for s in report.adjacent_suggestions
        )
        # gap_skill should surface as a skill gap (3 EXPLORATORY postings)
        assert any(g.skill_id == "gap_skill" for g in report.skill_gaps)
    finally:
        t.close()


def test_report_to_markdown(tmp_path):
    """to_markdown produces well-formed sections regardless of content."""
    t = Tracker(profile_id="test_orch_b", db_path=tmp_path / "t.db")
    try:
        report = ExpansionOrchestrator(
            tracker=t, profile={}, inventory_skill_ids=set(),
        ).run(days_back=7)
        md = report.to_markdown()
        # Section headers always present.
        assert "# Discovery Expansion Report" in md
        assert "## 1. Title clusters" in md
        assert "## 2. Go-deep employer candidates" in md
        assert "## 3. Adjacent-employer suggestions" in md
        assert "## 4. Inventory skill gaps" in md
        # Empty-state messaging for each section.
        assert "No clusters" in md
    finally:
        t.close()


def test_empty_data_produces_empty_report(tmp_path):
    """Run on an empty tracker — report fields are all empty lists."""
    t = Tracker(profile_id="test_orch_c", db_path=tmp_path / "t.db")
    try:
        report = ExpansionOrchestrator(
            tracker=t, profile={}, inventory_skill_ids=set(),
        ).run(days_back=14)
        assert report.title_clusters == []
        assert report.deep_targets == []
        assert report.adjacent_suggestions == []
        assert report.skill_gaps == []
        assert report.days_analyzed == 14
    finally:
        t.close()


def test_days_back_propagated(tmp_path):
    """days_back filter excludes evaluations outside the window for
    every strategy. Seed old + recent postings; only recent ones
    should appear."""
    t = Tracker(profile_id="test_orch_d", db_path=tmp_path / "t.db")
    try:
        old = (
            datetime.now(timezone.utc) - timedelta(days=45)
        ).isoformat()
        common = ["s1", "s2", "s3", "s4", "s5"]
        # Recent TOP_TIER × 3 — should produce a cluster + a deep target
        for emp in ("A", "B", "C"):
            _seed(t, employer=emp, title="Recent Role",
                  tier="TOP_TIER", skill_ids=common,
                  industry="financial_services", city="toronto")
        # Old TOP_TIER × 3 — should NOT count
        for emp in ("X", "Y", "Z"):
            oid = _seed(t, employer=emp, title="Old Role",
                        tier="TOP_TIER", skill_ids=common,
                        industry="financial_services", city="toronto")
            t._execute(
                "UPDATE eval_decisions SET evaluated_at = ? "
                "WHERE opportunity_id = ?",
                (old, oid),
            )

        report = ExpansionOrchestrator(
            tracker=t, profile={"target_cities": ["toronto"]},
            inventory_skill_ids=set(common),
        ).run(days_back=14)

        cluster_titles = {c.modal_title for c in report.title_clusters}
        assert cluster_titles == {"Recent Role"}
        deep_names = {d.company_name for d in report.deep_targets}
        # Only individual companies A/B/C have a recent TOP_TIER, each
        # with 1 TOP_TIER each — below min_count=2.
        # The strategy aggregates by company, so deep_names should be
        # empty given each recent employer only has 1 posting.
        assert deep_names == set()
    finally:
        t.close()
