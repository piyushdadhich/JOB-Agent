"""Unit tests for engine/expansion/employer_deep.py (Spec D1 TASK 2)."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.expansion.employer_deep import (  # noqa: E402
    EmployerDeepPromoter,
)
from engine.persistence.tracker import Tracker  # noqa: E402


def _seed(
    tracker, *, employer: str, title: str,
    tier: str, fit_score: int = 8,
    evaluated_at: str | None = None,
    ats_platform: str | None = None,
    ats_slug: str | None = None,
    is_deep_target: bool = False,
) -> int:
    cid = tracker.upsert_company(name=employer)
    if ats_platform is not None or ats_slug is not None:
        tracker.update_company_ats(
            cid, ats_platform=ats_platform, ats_slug=ats_slug,
        )
    if is_deep_target:
        tracker.set_company_deep_target(cid, True)
    oid, _ = tracker.insert_opportunity(
        company_id=cid, source="test",
        source_url=f"https://example.test/{employer}/{title}/{fit_score}",
        title=title,
    )
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


def test_identifies_company_with_2_strong(tmp_path):
    t = Tracker(profile_id="test_deep_a", db_path=tmp_path / "t.db")
    try:
        _seed(t, employer="DeepCo", title="Role A", tier="STRONG")
        _seed(t, employer="DeepCo", title="Role B", tier="TOP_TIER")
        out = EmployerDeepPromoter(t).identify(days_back=30)
        assert len(out) == 1
        assert out[0].company_name == "DeepCo"
        assert out[0].total_scored == 2
        assert out[0].strong_count == 1
        assert out[0].top_tier_count == 1
    finally:
        t.close()


def test_ignores_company_with_1_strong(tmp_path):
    t = Tracker(profile_id="test_deep_b", db_path=tmp_path / "t.db")
    try:
        _seed(t, employer="SoloCo", title="Role A", tier="STRONG")
        _seed(t, employer="SkipCo", title="Role B", tier="SKIP")
        out = EmployerDeepPromoter(t).identify(days_back=30)
        assert out == []
    finally:
        t.close()


def test_includes_ats_info(tmp_path):
    t = Tracker(profile_id="test_deep_ats", db_path=tmp_path / "t.db")
    try:
        _seed(t, employer="AtsCo", title="A", tier="STRONG",
              ats_platform="greenhouse", ats_slug="atsco")
        _seed(t, employer="AtsCo", title="B", tier="STRONG")
        out = EmployerDeepPromoter(t).identify(days_back=30)
        assert len(out) == 1
        assert out[0].ats_platform == "greenhouse"
        assert out[0].ats_slug == "atsco"
    finally:
        t.close()


def test_already_watched_flag(tmp_path):
    t = Tracker(profile_id="test_deep_watched", db_path=tmp_path / "t.db")
    try:
        _seed(t, employer="WatchedCo", title="A", tier="STRONG",
              is_deep_target=True)
        _seed(t, employer="WatchedCo", title="B", tier="STRONG")
        _seed(t, employer="NewCo", title="C", tier="STRONG")
        _seed(t, employer="NewCo", title="D", tier="STRONG")
        out = EmployerDeepPromoter(t).identify(days_back=30)
        watched = {d.company_name: d.already_watched for d in out}
        assert watched == {"WatchedCo": True, "NewCo": False}
    finally:
        t.close()


def test_days_back_filter(tmp_path):
    t = Tracker(profile_id="test_deep_days", db_path=tmp_path / "t.db")
    try:
        old = (
            datetime.now(timezone.utc) - timedelta(days=45)
        ).isoformat()
        # Old qualifying postings outside the window
        _seed(t, employer="OldCo", title="A", tier="STRONG",
              evaluated_at=old)
        _seed(t, employer="OldCo", title="B", tier="STRONG",
              evaluated_at=old)
        # Recent qualifying postings
        _seed(t, employer="NewCo", title="C", tier="STRONG")
        _seed(t, employer="NewCo", title="D", tier="STRONG")
        out = EmployerDeepPromoter(t).identify(days_back=14)
        assert [d.company_name for d in out] == ["NewCo"]
    finally:
        t.close()


def test_empty_returns_empty(tmp_path):
    t = Tracker(profile_id="test_deep_empty", db_path=tmp_path / "t.db")
    try:
        assert EmployerDeepPromoter(t).identify(days_back=30) == []
    finally:
        t.close()
