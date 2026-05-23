"""End-to-end test for scripts/recall_application.py."""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.applicant.storage import save_application  # noqa: E402
from engine.persistence.tracker import Tracker  # noqa: E402


def test_recall_cli_prints_resume_and_cover_letter(
    tmp_path, monkeypatch, capsys,
):
    db = tmp_path / "t.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        cid = t.upsert_company("Acme")
        oid, _ = t.insert_opportunity(
            company_id=cid, source="manual_entry",
            source_url="https://x", title="PM",
            location="Toronto", posting_text="x",
        )
        save_application(
            t, opportunity_id=oid, resume_variant="r.docx",
            resume_text="RESUME-TEXT-XYZ",
            cover_letter_text="CL-TEXT-ABC",
            ats_platform="greenhouse",
            screening_answers={"Years?": "10+"},
        )
    finally:
        t.close()

    from scripts import recall_application as mod
    monkeypatch.setattr(
        mod, "Tracker",
        lambda profile, db_path=None: Tracker(
            profile_id=profile, db_path=db,
        ),
    )
    rc = mod.main(["--profile", "p", "--posting", str(oid)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "RESUME-TEXT-XYZ" in out
    assert "CL-TEXT-ABC" in out
    assert "Years?" in out
    assert "10+" in out


def test_recall_cli_returns_1_when_no_application(
    tmp_path, monkeypatch, capsys,
):
    db = tmp_path / "t.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        cid = t.upsert_company("Acme")
        oid, _ = t.insert_opportunity(
            company_id=cid, source="manual_entry",
            source_url="https://x", title="PM",
            location="Toronto", posting_text="x",
        )
    finally:
        t.close()

    from scripts import recall_application as mod
    monkeypatch.setattr(
        mod, "Tracker",
        lambda profile, db_path=None: Tracker(
            profile_id=profile, db_path=db,
        ),
    )
    rc = mod.main(["--profile", "p", "--posting", str(oid)])
    assert rc == 1
    assert "No application found" in capsys.readouterr().out
