"""Tests for engine.persistence.opportunities.persist_record."""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.discovery.base import OpportunityRecord
from engine.persistence.opportunities import (
    IncompleteRecordError,
    normalize_employer_name,
    persist_record,
)
from engine.persistence.tracker import Tracker


@pytest.fixture
def tracker(tmp_path):
    db = tmp_path / "tracker.db"
    t = Tracker(profile_id="test", db_path=db)
    yield t
    t.close()


def _record(**overrides) -> OpportunityRecord:
    defaults = dict(
        source="jobspy",
        source_url="https://example.com/jobs/1",
        employer="Acme Corp",
        title="Delivery Manager",
        location="Toronto, ON",
        posting_text="Lead delivery teams.",
        date_discovered=datetime.now(timezone.utc),
        source_id="abc123",
        posted_at=date(2026, 4, 28),
        is_remote=False,
        salary_min=90000.0,
        salary_max=120000.0,
        salary_currency="CAD",
        salary_interval="yearly",
        employer_industry="Technology",
        raw_payload={"company": "Acme Corp", "job_url": "https://example.com/jobs/1"},
        search_context={"city": "toronto", "role_type": "delivery_manager"},
    )
    defaults.update(overrides)
    return OpportunityRecord(**defaults)


def test_persist_record_round_trip_required_fields(tracker):
    opp_id, was_new = persist_record(tracker, _record())
    assert was_new is True
    row = tracker.get_opportunity_by_id(opp_id)
    assert row["title"] == "Delivery Manager"
    assert row["source_url"] == "https://example.com/jobs/1"
    assert row["employer"] == "Acme Corp"


def test_persist_record_round_trip_all_optional_fields(tracker):
    opp_id, _ = persist_record(tracker, _record())
    row = tracker.get_opportunity_by_id(opp_id)
    assert row["source_id"] == "abc123"
    assert row["location"] == "Toronto, ON"
    assert row["is_remote"] == 0
    assert row["salary_min"] == 90000.0
    assert row["salary_max"] == 120000.0
    assert row["salary_currency"] == "CAD"
    assert row["salary_interval"] == "yearly"
    assert row["posted_at"] == "2026-04-28"


def test_persist_record_creates_company(tracker):
    persist_record(tracker, _record(employer="NewCo"))
    company = tracker.get_company_by_name("NewCo")
    assert company is not None
    assert company["industry"] == "Technology"


def test_persist_record_reuses_existing_company(tracker):
    persist_record(tracker, _record(source_url="https://x/1"))
    persist_record(tracker, _record(source_url="https://x/2"))
    assert tracker.count_companies() == 1


def test_persist_record_was_new_true_first_time(tracker):
    _, was_new = persist_record(tracker, _record())
    assert was_new is True


def test_persist_record_was_new_false_second_time(tracker):
    persist_record(tracker, _record())
    _, was_new = persist_record(tracker, _record())
    assert was_new is False


def test_persist_record_handles_datetime_posted_at(tracker):
    dt = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)
    opp_id, _ = persist_record(tracker, _record(posted_at=dt))
    row = tracker.get_opportunity_by_id(opp_id)
    assert row["posted_at"].startswith("2026-04-28T12:00:00")


def test_persist_record_handles_string_posted_at(tracker):
    opp_id, _ = persist_record(tracker, _record(posted_at="2026-04-28"))
    row = tracker.get_opportunity_by_id(opp_id)
    assert row["posted_at"] == "2026-04-28"


def test_persist_record_handles_none_optional_fields(tracker):
    rec = _record(
        source_id=None, location=None, posting_text=None,
        is_remote=None, posted_at=None,
        salary_min=None, salary_max=None,
        salary_currency=None, salary_interval=None,
        employer_industry=None,
    )
    opp_id, was_new = persist_record(tracker, rec)
    assert was_new is True
    row = tracker.get_opportunity_by_id(opp_id)
    assert row["source_id"] is None
    assert row["salary_min"] is None
    assert row["posted_at"] is None


def test_persist_record_serializes_search_context(tracker):
    opp_id, _ = persist_record(tracker, _record())
    row = tracker.get_opportunity_by_id(opp_id)
    assert json.loads(row["search_context"]) == {
        "city": "toronto", "role_type": "delivery_manager",
    }


def test_normalize_employer_name_lowercases_and_strips():
    assert normalize_employer_name("  Acme Corp  ") == "acme corp"


def test_normalize_employer_name_collapses_whitespace():
    assert normalize_employer_name("Acme   Corp\tInc") == "acme corp inc"


# ----- Incomplete record validation ---------------------------------------

def test_persist_record_raises_incomplete_when_employer_empty(tracker):
    rec = _record(employer="")
    with pytest.raises(IncompleteRecordError, match="employer"):
        persist_record(tracker, rec)


def test_persist_record_raises_incomplete_when_employer_whitespace_only(tracker):
    rec = _record(employer="   ")
    with pytest.raises(IncompleteRecordError, match="employer"):
        persist_record(tracker, rec)


def test_persist_record_raises_incomplete_when_source_url_empty(tracker):
    rec = _record(source_url="")
    with pytest.raises(IncompleteRecordError, match="source_url"):
        persist_record(tracker, rec)


def test_persist_record_raises_incomplete_when_title_empty(tracker):
    rec = _record(title="")
    with pytest.raises(IncompleteRecordError, match="title"):
        persist_record(tracker, rec)


def test_incomplete_record_error_message_includes_url(tracker):
    rec = _record(employer="", source_url="https://example.com/jobs/77")
    with pytest.raises(IncompleteRecordError) as excinfo:
        persist_record(tracker, rec)
    assert "https://example.com/jobs/77" in str(excinfo.value)


def test_incomplete_record_error_is_value_error_subclass():
    err = IncompleteRecordError("x")
    assert isinstance(err, ValueError)
