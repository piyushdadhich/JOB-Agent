"""Tests for engine.applicant.storage save/recall round-trip."""
from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.applicant.storage import (  # noqa: E402
    recall_application,
    save_application,
)
from engine.persistence.tracker import Tracker  # noqa: E402


def _seed_opportunity(tracker, employer="Acme", title="Senior PM"):
    cid = tracker.upsert_company(employer)
    opp_id, _ = tracker.insert_opportunity(
        company_id=cid, source="manual_entry",
        source_url=f"https://x/{employer}", title=title,
        location="Toronto", posting_text="Lead delivery.",
    )
    return opp_id


def test_save_application_writes_v29_columns(tmp_path):
    t = Tracker(profile_id="p", db_path=tmp_path / "t.db")
    try:
        oid = _seed_opportunity(t)
        app_id = save_application(
            t, opportunity_id=oid,
            resume_variant="resume_v1.docx",
            resume_text="My resume.",
            cover_letter_text="Dear team,",
            ats_platform="greenhouse",
            screenshot_path="screens/1.png",
            submitted_url="https://confirmed/1",
            screening_answers={"Q1": "A1"},
        )
        row = t.get_application_by_id(app_id)
        assert row["resume_text"] == "My resume."
        assert row["cover_letter_text"] == "Dear team,"
        assert row["ats_platform"] == "greenhouse"
        assert row["submitted_url"] == "https://confirmed/1"
        assert row["status"] == "submitted"
    finally:
        t.close()


def test_recall_application_returns_full_record(tmp_path):
    t = Tracker(profile_id="p", db_path=tmp_path / "t.db")
    try:
        oid = _seed_opportunity(
            t, employer="BMO", title="Delivery Lead",
        )
        save_application(
            t, opportunity_id=oid,
            resume_variant="resume.docx",
            resume_text="RESUME-TEXT",
            cover_letter_text="CL-TEXT",
            ats_platform="workday",
            screening_answers={"Years?": "10+"},
        )
        row = recall_application(t, opportunity_id=oid)
        assert row is not None
        assert row["employer"] == "BMO"
        assert row["opportunity_title"] == "Delivery Lead"
        assert row["resume_text"] == "RESUME-TEXT"
        assert row["cover_letter_text"] == "CL-TEXT"
        assert row["screening_answers"] == {"Years?": "10+"}
    finally:
        t.close()


def test_recall_returns_none_when_no_application(tmp_path):
    t = Tracker(profile_id="p", db_path=tmp_path / "t.db")
    try:
        oid = _seed_opportunity(t)
        assert recall_application(t, opportunity_id=oid) is None
    finally:
        t.close()


def test_recall_returns_most_recent_when_multiple(tmp_path):
    t = Tracker(profile_id="p", db_path=tmp_path / "t.db")
    try:
        oid = _seed_opportunity(t)
        save_application(
            t, opportunity_id=oid, resume_variant="r1.docx",
            resume_text="OLD", cover_letter_text="OLD-CL",
            ats_platform="greenhouse",
        )
        time.sleep(0.05)
        save_application(
            t, opportunity_id=oid, resume_variant="r2.docx",
            resume_text="NEW", cover_letter_text="NEW-CL",
            ats_platform="lever",
        )
        row = recall_application(t, opportunity_id=oid)
        assert row["resume_text"] == "NEW"
        assert row["ats_platform"] == "lever"
    finally:
        t.close()
