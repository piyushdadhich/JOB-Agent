"""Unit tests for scripts/apply_batch.py."""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.persistence.tracker import Tracker  # noqa: E402

# Imported lazily inside tests where we need to monkeypatch its
# Tracker / apply._run dependencies.


def _seed_opp_with_eval(
    tracker, employer="Acme", title="PM", tier="STRONG",
    fit_score=8, status="new",
):
    cid = tracker.upsert_company(employer)
    opp_id, _ = tracker.insert_opportunity(
        company_id=cid, source="manual_entry",
        source_url=f"https://x/{employer}/{title}", title=title,
        location="Toronto", posting_text="x",
    )
    if status != "new":
        tracker.update_opportunity_status(opp_id, status)
    tracker.record_evaluation(
        opportunity_id=opp_id,
        evaluator_version="pipeline-v2.3.0",
        tier=tier, fit_score=fit_score,
        sector=None, role_type=None,
        stage_trace={}, reasoning="x",
    )
    return opp_id


def _ns(**overrides):
    base = dict(
        profile="p", postings=None, tier=None, limit=None, dry_run=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


# --- resolve_posting_ids -----------------------------------------

def test_resolves_explicit_posting_list(tmp_path):
    from scripts import apply_batch as mod
    t = Tracker(profile_id="p", db_path=tmp_path / "t.db")
    try:
        ids = mod.resolve_posting_ids(
            t, _ns(postings="11,22,33"),
        )
    finally:
        t.close()
    assert ids == [11, 22, 33]


def test_resolves_tier_to_active_strong_excluding_applied(tmp_path):
    from scripts import apply_batch as mod
    t = Tracker(profile_id="p", db_path=tmp_path / "t.db")
    try:
        a = _seed_opp_with_eval(t, "Acme", "Senior PM", "STRONG", 9)
        b = _seed_opp_with_eval(
            t, "Beta", "Delivery Lead", "STRONG", 7,
        )
        c = _seed_opp_with_eval(
            t, "Gamma", "BA", "EXPLORATORY", 4,
        )  # filtered out
        # Already applied to b -> excluded.
        t.create_application(b, "resume.docx")

        ids = mod.resolve_posting_ids(t, _ns(tier="STRONG"))
    finally:
        t.close()
    assert ids == [a]
    assert b not in ids
    assert c not in ids


def test_returns_nonzero_when_no_postings_selected(tmp_path, monkeypatch):
    from scripts import apply_batch as mod
    monkeypatch.setattr(
        mod, "Tracker",
        lambda profile, db_path=None: Tracker(
            profile_id=profile, db_path=tmp_path / "t.db",
        ),
    )
    rc = asyncio.run(mod.main_async(_ns()))
    assert rc == 2


# --- daily cap ---------------------------------------------------

def test_aborts_when_daily_cap_already_reached(
    tmp_path, monkeypatch, capsys,
):
    from scripts import apply_batch as mod
    db = tmp_path / "t.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        # Create one submitted application dated today.
        cid = t.upsert_company("Co")
        oid, _ = t.insert_opportunity(
            company_id=cid, source="manual_entry",
            source_url="https://x", title="PM",
            location="Toronto", posting_text="x",
        )
        app_id = t.create_application(oid, "r.docx")
        t.update_application_status(app_id, "submitted")
    finally:
        t.close()

    monkeypatch.setattr(
        mod, "Tracker",
        lambda profile, db_path=None: Tracker(
            profile_id=profile, db_path=db,
        ),
    )
    monkeypatch.setattr(
        mod, "_profile_yaml",
        lambda _: {"applicant": {"daily_application_cap": 1}},
    )
    rc = asyncio.run(mod.main_async(_ns(postings="999")))
    assert rc == 1
    assert "cap reached" in capsys.readouterr().out.lower()


def test_dry_run_proceeds_even_when_cap_reached(
    tmp_path, monkeypatch, capsys,
):
    """Dry-run is for previewing -- should not abort on the cap."""
    from scripts import apply_batch as mod
    db = tmp_path / "t.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        cid = t.upsert_company("Co")
        oid, _ = t.insert_opportunity(
            company_id=cid, source="manual_entry",
            source_url="https://x", title="PM",
            location="Toronto", posting_text="x",
        )
        app_id = t.create_application(oid, "r.docx")
        t.update_application_status(app_id, "submitted")
    finally:
        t.close()

    monkeypatch.setattr(
        mod, "Tracker",
        lambda profile, db_path=None: Tracker(
            profile_id=profile, db_path=db,
        ),
    )
    monkeypatch.setattr(
        mod, "_profile_yaml",
        lambda _: {"applicant": {"daily_application_cap": 1}},
    )

    async def fake_run_one(profile, posting_id, dry_run):
        return 0
    monkeypatch.setattr(mod, "_run_one", fake_run_one)

    rc = asyncio.run(
        mod.main_async(_ns(postings="42", dry_run=True)),
    )
    assert rc == 0


# --- daily cap slicing -------------------------------------------

def test_respects_daily_cap_when_postings_exceed_budget(
    tmp_path, monkeypatch,
):
    from scripts import apply_batch as mod
    db = tmp_path / "t.db"
    monkeypatch.setattr(
        mod, "Tracker",
        lambda profile, db_path=None: Tracker(
            profile_id=profile, db_path=db,
        ),
    )
    monkeypatch.setattr(
        mod, "_profile_yaml",
        lambda _: {"applicant": {"daily_application_cap": 2}},
    )

    calls: list[int] = []

    async def fake_run_one(profile, posting_id, dry_run):
        calls.append(posting_id)
        return 0
    monkeypatch.setattr(mod, "_run_one", fake_run_one)

    rc = asyncio.run(
        mod.main_async(_ns(postings="1,2,3,4,5")),
    )
    assert rc == 0
    # Cap=2 + 0 already today -> only 2 should run.
    assert calls == [1, 2]


# --- summary output ----------------------------------------------

def test_summary_prints_counts(tmp_path, monkeypatch, capsys):
    from scripts import apply_batch as mod
    db = tmp_path / "t.db"
    monkeypatch.setattr(
        mod, "Tracker",
        lambda profile, db_path=None: Tracker(
            profile_id=profile, db_path=db,
        ),
    )
    monkeypatch.setattr(
        mod, "_profile_yaml",
        lambda _: {"applicant": {"daily_application_cap": 10}},
    )

    async def fake_run_one(profile, posting_id, dry_run):
        return 0 if posting_id < 100 else 1
    monkeypatch.setattr(mod, "_run_one", fake_run_one)

    asyncio.run(mod.main_async(_ns(postings="1,2,200")))
    out = capsys.readouterr().out
    assert "Submitted:     2" in out
    assert "Skipped:       1" in out
    assert "Cap remaining" in out
