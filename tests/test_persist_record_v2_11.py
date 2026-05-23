"""Tests for persist_record + classification stamping (v2.11)."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.discovery.base import OpportunityRecord  # noqa: E402
from engine.persistence.opportunities import persist_record  # noqa: E402
from engine.persistence.tracker import Tracker  # noqa: E402


def _record(**overrides) -> OpportunityRecord:
    base = dict(
        source="manual_entry",
        source_url="https://example.com/1",
        employer="Acme",
        title="Project Manager",
        location="Toronto, ON",
        posting_text="some posting text",
        date_discovered=datetime(2026, 5, 9, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return OpportunityRecord(**base)


def test_persist_record_stamps_city_and_priority_2(tmp_path):
    t = Tracker(profile_id="p", db_path=tmp_path / "t.db")
    try:
        opp_id, was_new = persist_record(t, _record())
        assert was_new is True
        row = t._query_one(
            "SELECT city, ai_subtype, eval_priority "
            "FROM opportunities WHERE id = ?",
            (opp_id,),
        )
        assert row["city"] == "Toronto"
        assert row["ai_subtype"] is None
        assert row["eval_priority"] == 2
    finally:
        t.close()


def test_persist_record_ai_title_yields_priority_1(tmp_path):
    t = Tracker(profile_id="p", db_path=tmp_path / "t.db")
    try:
        opp_id, _ = persist_record(t, _record(
            title="AI Engineer",
            source_url="https://example.com/ai",
        ))
        row = t._query_one(
            "SELECT city, ai_subtype, eval_priority "
            "FROM opportunities WHERE id = ?",
            (opp_id,),
        )
        assert row["city"] == "Toronto"
        assert row["ai_subtype"] == "ai_engineer"
        assert row["eval_priority"] == 1
    finally:
        t.close()


def test_persist_record_unknown_city_leaves_city_null(tmp_path):
    t = Tracker(profile_id="p", db_path=tmp_path / "t.db")
    try:
        opp_id, _ = persist_record(t, _record(
            location="Halifax, NS",
            source_url="https://example.com/h",
        ))
        row = t._query_one(
            "SELECT city, eval_priority "
            "FROM opportunities WHERE id = ?",
            (opp_id,),
        )
        assert row["city"] is None
        assert row["eval_priority"] == 2
    finally:
        t.close()
