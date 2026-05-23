"""Tests for v2.14 job_posters / opportunity_posters persistence.

Covers:
  - Tracker.upsert_job_poster (insert + idempotent updates)
  - Tracker.link_opportunity_to_poster (insert + idempotent PK)
  - persist_record integration: hiring_team in raw_payload creates
    job_posters + opportunity_posters rows + writes hiring_team_json.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.discovery.base import OpportunityRecord  # noqa: E402
from engine.persistence.opportunities import persist_record  # noqa: E402
from engine.persistence.tracker import Tracker  # noqa: E402


def _open(tmp_path):
    return Tracker(profile_id="p", db_path=tmp_path / "t.db")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_record(
    *,
    title="Project Manager",
    employer="Acme Corp",
    url="https://www.linkedin.com/jobs/view/12345",
    hiring_team=None,
) -> OpportunityRecord:
    return OpportunityRecord(
        source="linkedin_guest",
        source_url=url,
        employer=employer,
        title=title,
        location="Toronto, ON",
        posting_text="Body.",
        date_discovered=datetime.now(timezone.utc),
        raw_payload={"hiring_team": hiring_team or []},
        search_context={},
    )


# ---------- Tracker.upsert_job_poster ----------

def test_upsert_job_poster_inserts_new(tmp_path):
    t = _open(tmp_path)
    try:
        observed = _now()
        pid = t.upsert_job_poster(
            name="Jane Smith",
            title="VP Engineering",
            profile_url="https://www.linkedin.com/in/jane-smith",
            employer="Acme Corp",
            observed_at=observed,
        )
        assert pid > 0
        row = t.find_job_poster_by_url(
            "https://www.linkedin.com/in/jane-smith"
        )
        assert row is not None
        assert row["full_name"] == "Jane Smith"
        assert row["title_at_posting"] == "VP Engineering"
        assert row["employer_at_posting"] == "Acme Corp"
        assert row["total_postings_observed"] == 1
    finally:
        t.close()


def test_upsert_job_poster_idempotent_by_url(tmp_path):
    t = _open(tmp_path)
    try:
        url = "https://www.linkedin.com/in/foo"
        pid1 = t.upsert_job_poster(
            name="Foo", title="A", profile_url=url,
            employer="ACo", observed_at=_now(),
        )
        # Different name+employer but same URL -> same row.
        pid2 = t.upsert_job_poster(
            name="Foo Renamed", title="B", profile_url=url,
            employer="OtherCo", observed_at=_now(),
        )
        assert pid1 == pid2
        rows = t._query_all("SELECT * FROM job_posters")
        assert len(rows) == 1
    finally:
        t.close()


def test_upsert_job_poster_idempotent_by_name_employer_when_no_url(tmp_path):
    t = _open(tmp_path)
    try:
        pid1 = t.upsert_job_poster(
            name="No URL", title="A", profile_url=None,
            employer="SameCo", observed_at=_now(),
        )
        pid2 = t.upsert_job_poster(
            name="No URL", title="B", profile_url=None,
            employer="SameCo", observed_at=_now(),
        )
        assert pid1 == pid2
        rows = t._query_all("SELECT * FROM job_posters")
        assert len(rows) == 1
    finally:
        t.close()


def test_upsert_job_poster_increments_total_postings_observed(tmp_path):
    t = _open(tmp_path)
    try:
        url = "https://www.linkedin.com/in/repeat"
        for _ in range(3):
            t.upsert_job_poster(
                name="Re Peat", title="PM", profile_url=url,
                employer="ACo", observed_at=_now(),
            )
        row = t.find_job_poster_by_url(url)
        assert row["total_postings_observed"] == 3
    finally:
        t.close()


def test_upsert_job_poster_updates_last_seen_at(tmp_path):
    t = _open(tmp_path)
    try:
        url = "https://www.linkedin.com/in/seen"
        first = "2026-04-01T00:00:00+00:00"
        last = "2026-05-13T00:00:00+00:00"
        t.upsert_job_poster(
            name="Person", title="PM", profile_url=url,
            employer="ACo", observed_at=first,
        )
        t.upsert_job_poster(
            name="Person", title="PM", profile_url=url,
            employer="ACo", observed_at=last,
        )
        row = t.find_job_poster_by_url(url)
        assert row["first_seen_at"] != last
        assert row["last_seen_at"] == last
    finally:
        t.close()


# ---------- Tracker.link_opportunity_to_poster ----------

def test_link_opportunity_to_poster_inserts(tmp_path):
    t = _open(tmp_path)
    try:
        # Need a real opportunity to satisfy the FK.
        company_id = t.upsert_company(name="ACo")
        opp_id, _ = t.insert_opportunity(
            company_id=company_id, source="linkedin_guest",
            source_url="https://www.linkedin.com/jobs/view/1",
            title="PM",
        )
        poster_id = t.upsert_job_poster(
            name="X", title=None, profile_url="https://li.com/x",
            employer="ACo", observed_at=_now(),
        )
        t.link_opportunity_to_poster(
            opportunity_id=opp_id,
            job_poster_id=poster_id,
            role_on_posting="hiring_team",
            observed_at=_now(),
        )
        rows = t._query_all(
            "SELECT * FROM opportunity_posters WHERE opportunity_id = ?",
            (opp_id,),
        )
        assert len(rows) == 1
    finally:
        t.close()


def test_link_opportunity_to_poster_idempotent(tmp_path):
    t = _open(tmp_path)
    try:
        company_id = t.upsert_company(name="ACo")
        opp_id, _ = t.insert_opportunity(
            company_id=company_id, source="linkedin_guest",
            source_url="https://www.linkedin.com/jobs/view/2",
            title="PM",
        )
        poster_id = t.upsert_job_poster(
            name="X", title=None, profile_url="https://li.com/x",
            employer="ACo", observed_at=_now(),
        )
        # Same triple inserted three times — only one row should remain.
        for _ in range(3):
            t.link_opportunity_to_poster(
                opportunity_id=opp_id,
                job_poster_id=poster_id,
                role_on_posting="hiring_team",
                observed_at=_now(),
            )
        rows = t._query_all(
            "SELECT * FROM opportunity_posters WHERE opportunity_id = ?",
            (opp_id,),
        )
        assert len(rows) == 1
    finally:
        t.close()


# ---------- persist_record integration ----------

def test_persist_record_creates_job_posters_for_hiring_team(tmp_path):
    t = _open(tmp_path)
    try:
        rec = _make_record(hiring_team=[
            {
                "name": "Alice",
                "title": "Director",
                "profile_url": "https://www.linkedin.com/in/alice",
                "role": "hiring_team",
            },
            {
                "name": "Bob",
                "title": None,
                "profile_url": None,
                "role": "posted_by",
            },
        ])
        opp_id, _ = persist_record(t, rec)
        # Two posters created.
        rows = t._query_all("SELECT * FROM job_posters")
        assert len(rows) == 2
        # Two links to this opportunity.
        links = t._query_all(
            "SELECT * FROM opportunity_posters WHERE opportunity_id = ?",
            (opp_id,),
        )
        assert len(links) == 2
        roles = sorted(r["role_on_posting"] for r in links)
        assert roles == ["hiring_team", "posted_by"]
    finally:
        t.close()


def test_persist_record_skips_hiring_team_without_name(tmp_path):
    t = _open(tmp_path)
    try:
        rec = _make_record(hiring_team=[
            {"name": "", "title": "x", "role": "hiring_team"},
            {"title": "y", "role": "hiring_team"},  # name missing entirely
            {"name": "Real Person", "title": "z", "role": "hiring_team"},
        ])
        opp_id, _ = persist_record(t, rec)
        rows = t._query_all("SELECT * FROM job_posters")
        assert len(rows) == 1
        assert rows[0]["full_name"] == "Real Person"
    finally:
        t.close()


def test_persist_record_writes_hiring_team_json_column(tmp_path):
    t = _open(tmp_path)
    try:
        team = [
            {
                "name": "Cleo", "title": "CTO",
                "profile_url": "https://www.linkedin.com/in/cleo",
                "role": "hiring_team",
            },
        ]
        rec = _make_record(hiring_team=team)
        opp_id, _ = persist_record(t, rec)
        row = t._query_one(
            "SELECT hiring_team_json FROM opportunities WHERE id = ?",
            (opp_id,),
        )
        import json
        loaded = json.loads(row["hiring_team_json"])
        assert loaded == team
    finally:
        t.close()
