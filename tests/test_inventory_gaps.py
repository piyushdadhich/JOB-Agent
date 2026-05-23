"""Unit tests for engine/expansion/inventory_gaps.py (Spec D1 TASK 4)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.expansion.inventory_gaps import (  # noqa: E402
    InventoryGapSurfer,
    SkillGap,
    _DEFAULT_ACTION_PROMPT,
)
from engine.persistence.tracker import Tracker  # noqa: E402


def _seed(
    tracker, *, title: str, employer: str, tier: str,
    skill_ids: list[str],
    fit_score: int = 5,
    evaluated_at: str | None = None,
) -> int:
    cid = tracker.upsert_company(name=employer)
    oid, _ = tracker.insert_opportunity(
        company_id=cid, source="test",
        source_url=f"https://example.test/{employer}/{title}/{fit_score}",
        title=title,
    )
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


def _seed_label(tracker, skill_id: str, label: str) -> None:
    tracker._execute(
        "INSERT OR REPLACE INTO skill_labels "
        "(skill_id, label, taxonomy, source) VALUES (?, ?, ?, ?)",
        (skill_id, label, "lightcast", "test"),
    )


def test_surfaces_recurring_missed_skills(tmp_path):
    """A skill not in inventory but appearing in N+ EXPLORATORY
    postings surfaces as a gap."""
    t = Tracker(profile_id="test_gap_a", db_path=tmp_path / "t.db")
    try:
        _seed_label(t, "KSGAP1", "GIS Mapping")
        for i, emp in enumerate(("A", "B", "C")):
            _seed(t, employer=emp, title=f"Land Officer {i}",
                  tier="EXPLORATORY",
                  skill_ids=["KSGAP1", "KS_owned"])
        gaps = InventoryGapSurfer(t, {"KS_owned"}).surface(
            days_back=30, min_occurrences=3,
        )
        assert len(gaps) == 1
        g = gaps[0]
        assert g.skill_id == "KSGAP1"
        assert g.skill_label == "GIS Mapping"
        assert g.occurrence_count == 3
        assert g.action_prompt == _DEFAULT_ACTION_PROMPT
    finally:
        t.close()


def test_ignores_skills_in_inventory(tmp_path):
    """Skills already in inventory are not surfaced as gaps."""
    t = Tracker(profile_id="test_gap_b", db_path=tmp_path / "t.db")
    try:
        for i, emp in enumerate(("A", "B", "C")):
            _seed(t, employer=emp, title=f"Role {i}",
                  tier="EXPLORATORY",
                  skill_ids=["KS_owned", "KS_other"])
        gaps = InventoryGapSurfer(
            t, {"KS_owned", "KS_other"},
        ).surface(days_back=30, min_occurrences=3)
        assert gaps == []
    finally:
        t.close()


def test_min_occurrences_filter(tmp_path):
    """Skills appearing fewer than min_occurrences times are dropped."""
    t = Tracker(profile_id="test_gap_c", db_path=tmp_path / "t.db")
    try:
        # KSGAP1 appears in 3 postings (qualifies at min=3)
        for i, emp in enumerate(("A", "B", "C")):
            _seed(t, employer=emp, title=f"Role {i}",
                  tier="EXPLORATORY",
                  skill_ids=["KSGAP1"])
        # KSGAP2 appears in 2 postings (below min=3)
        for i, emp in enumerate(("D", "E")):
            _seed(t, employer=emp, title=f"Role D {i}",
                  tier="EXPLORATORY",
                  skill_ids=["KSGAP2"])
        gaps = InventoryGapSurfer(t, set()).surface(
            days_back=30, min_occurrences=3,
        )
        ids = {g.skill_id for g in gaps}
        assert ids == {"KSGAP1"}
    finally:
        t.close()


def test_includes_example_postings(tmp_path):
    """The first up-to-5 example titles are returned."""
    t = Tracker(profile_id="test_gap_d", db_path=tmp_path / "t.db")
    try:
        for i, emp in enumerate(("A", "B", "C", "D", "E", "F", "G")):
            _seed(t, employer=emp, title=f"Land Officer {i}",
                  tier="EXPLORATORY",
                  skill_ids=["KSGAP1"])
        gaps = InventoryGapSurfer(t, set()).surface(
            days_back=30, min_occurrences=3,
        )
        assert len(gaps) == 1
        g = gaps[0]
        assert g.occurrence_count == 7
        assert len(g.example_postings) == 5
        for title in g.example_postings:
            assert title.startswith("Land Officer")
    finally:
        t.close()


def test_sorts_by_frequency(tmp_path):
    """Gaps are returned in decreasing occurrence_count order."""
    t = Tracker(profile_id="test_gap_e", db_path=tmp_path / "t.db")
    try:
        for i, emp in enumerate(("A", "B", "C")):
            _seed(t, employer=emp, title=f"Role A{i}",
                  tier="EXPLORATORY",
                  skill_ids=["KSLOW"])
        for i, emp in enumerate(("D", "E", "F", "G", "H")):
            _seed(t, employer=emp, title=f"Role B{i}",
                  tier="EXPLORATORY",
                  skill_ids=["KSHIGH"])
        gaps = InventoryGapSurfer(t, set()).surface(
            days_back=30, min_occurrences=3,
        )
        assert [g.skill_id for g in gaps] == ["KSHIGH", "KSLOW"]
    finally:
        t.close()


def test_empty_exploratory_returns_empty(tmp_path):
    """No EXPLORATORY postings => no gaps."""
    t = Tracker(profile_id="test_gap_f", db_path=tmp_path / "t.db")
    try:
        # Seed only STRONG postings, none EXPLORATORY.
        _seed(t, employer="A", title="A1", tier="STRONG",
              skill_ids=["KSGAP1"])
        _seed(t, employer="B", title="B1", tier="STRONG",
              skill_ids=["KSGAP1"])
        gaps = InventoryGapSurfer(t, set()).surface(
            days_back=30, min_occurrences=3,
        )
        assert gaps == []
    finally:
        t.close()
