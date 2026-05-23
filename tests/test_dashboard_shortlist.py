"""Tests for /api/shortlist GET / select / skip."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from dashboard.backend.app import create_app  # noqa: E402
from dashboard.backend.deps import get_tracker  # noqa: E402
from engine.persistence.tracker import Tracker  # noqa: E402


@pytest.fixture
def harness(tmp_path):
    """Return (TestClient, Tracker) wired to a tmp DB."""
    db = tmp_path / "t.db"
    tracker = Tracker(profile_id="p", db_path=db)
    app = create_app()

    def _override():
        # Yield without closing — fixture teardown closes the tracker.
        yield tracker

    app.dependency_overrides[get_tracker] = _override
    yield TestClient(app), tracker
    tracker.close()


def _seed_eval(
    tracker: Tracker, opp_id: int, tier: str, fit_score: int = 8,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    tracker._execute(
        "INSERT INTO eval_decisions "
        "(opportunity_id, evaluator_version, tier, fit_score, "
        " stage_trace, reasoning, evaluated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (opp_id, "test", tier, fit_score, "[]", "good", now),
    )


def _add_posting(tracker, employer, title, source_url):
    cid = tracker.upsert_company(employer)
    opp_id, _ = tracker.insert_opportunity(
        company_id=cid, source="manual_entry",
        source_url=source_url, title=title,
        location="Toronto", posting_text="Lead delivery.",
    )
    return opp_id


def _set_ai_subtype(tracker, opp_id, subtype):
    """Stamp ai_subtype on a row post-insert (the v2.11 classification
    columns aren't set by insert_opportunity directly)."""
    tracker._execute(
        "UPDATE opportunities SET ai_subtype = ? WHERE id = ?",
        (subtype, opp_id),
    )


def test_shortlist_surfaces_v220_eval_extras(harness):
    """Spec JA-1 TASK 2: letter_grade + red_flags + interview_plan +
    culture_signals come back parsed (not JSON-string) from the API."""
    import json as _json
    client, t = harness
    oid = _add_posting(t, "Acme Bank", "Senior PM", "https://x/jx1")
    plan = [{"topic": "Quality", "talking_point": "tp",
             "proof_point": "pp"}]
    flags = [{"flag": "Below market compensation", "severity": "medium"}]
    signals = [{"signal": "DEI", "sentiment": "positive"}]
    t.record_evaluation(
        opportunity_id=oid,
        evaluator_version="pipeline-v2.3.0",
        tier="TOP_TIER", fit_score=9,
        sector=None, role_type=None,
        stage_trace={}, reasoning="ok",
        letter_grade="A",
        interview_plan=plan, red_flags=flags,
        culture_signals=signals,
    )
    items = client.get("/api/shortlist").json()
    assert items, items
    row = items[0]
    assert row["letter_grade"] == "A"
    assert row["interview_plan"][0]["topic"] == "Quality"
    assert row["red_flags"][0]["flag"] == "Below market compensation"
    assert row["culture_signals"][0]["sentiment"] == "positive"
    # Defensive: ensure not still a JSON string.
    assert isinstance(row["interview_plan"], list)


def test_shortlist_returns_top_strong_and_exploratory(harness):
    # FIX-4: EXPLORATORY is now surfaced alongside TOP_TIER and
    # STRONG (the Shortlist page has an EXPLORATORY tab now).
    client, t = harness
    oid_top = _add_posting(t, "TD", "Senior PM", "https://x/1")
    oid_strong = _add_posting(t, "BMO", "Delivery Lead", "https://x/2")
    oid_explore = _add_posting(t, "CIBC", "Scrum Master", "https://x/3")
    _seed_eval(t, oid_top, "TOP_TIER", 9)
    _seed_eval(t, oid_strong, "STRONG", 7)
    _seed_eval(t, oid_explore, "EXPLORATORY", 5)

    r = client.get("/api/shortlist")
    assert r.status_code == 200
    items = r.json()
    assert [i["tier"] for i in items] == ["TOP_TIER", "STRONG", "EXPLORATORY"]
    assert items[0]["employer"] == "TD"
    assert items[0]["fit_score"] == 9
    assert items[1]["employer"] == "BMO"
    assert items[2]["employer"] == "CIBC"


def test_shortlist_excludes_dismissed_and_pursued(harness):
    client, t = harness
    oid = _add_posting(t, "TD", "PM", "https://x/1")
    _seed_eval(t, oid, "TOP_TIER", 9)
    t.update_opportunity_status(oid, "dismissed")
    assert client.get("/api/shortlist").json() == []


def test_shortlist_orders_top_tier_before_strong(harness):
    client, t = harness
    oid_strong = _add_posting(t, "BMO", "Lead", "https://x/1")
    oid_top = _add_posting(t, "TD", "PM", "https://x/2")
    _seed_eval(t, oid_strong, "STRONG", 9)
    _seed_eval(t, oid_top, "TOP_TIER", 7)
    items = client.get("/api/shortlist").json()
    # TOP_TIER comes first even with lower fit score.
    assert items[0]["tier"] == "TOP_TIER"


def test_select_creates_application_and_marks_shortlisted(harness):
    client, t = harness
    oid = _add_posting(t, "TD", "PM", "https://x/1")
    _seed_eval(t, oid, "TOP_TIER", 9)

    r = client.post(f"/api/shortlist/{oid}/select")
    assert r.status_code == 200
    body = r.json()
    assert body["application_id"] >= 1
    assert body["selected_at"]
    assert t.get_opportunity_by_id(oid)["status"] == "shortlisted"
    app = t.get_application_by_id(body["application_id"])
    assert app["selected_at"] is not None


def test_select_reuses_existing_application_row(harness):
    client, t = harness
    oid = _add_posting(t, "TD", "PM", "https://x/1")
    _seed_eval(t, oid, "TOP_TIER", 9)
    first = client.post(f"/api/shortlist/{oid}/select").json()
    second = client.post(f"/api/shortlist/{oid}/select").json()
    assert first["application_id"] == second["application_id"]


def test_select_404_on_unknown_posting(harness):
    client, _ = harness
    r = client.post("/api/shortlist/999/select")
    assert r.status_code == 404


def test_filter_ai_role_only_returns_postings_with_subtype(harness):
    """Spec 6b TASK 3.5: ai_role=ai_only filters to postings where
    ai_subtype IS NOT NULL (coarse AI signal)."""
    client, t = harness
    a1 = _add_posting(t, "AI Co A", "ML Engineer", "https://x/a1")
    a2 = _add_posting(t, "AI Co B", "AI Research", "https://x/a2")
    n1 = _add_posting(t, "Bank", "Senior PM", "https://x/n1")
    n2 = _add_posting(t, "Telco", "Delivery Lead", "https://x/n2")
    _set_ai_subtype(t, a1, "ai_engineer")
    _set_ai_subtype(t, a2, "ml_general")
    _seed_eval(t, a1, "TOP_TIER", 9)
    _seed_eval(t, a2, "STRONG", 8)
    _seed_eval(t, n1, "TOP_TIER", 9)
    _seed_eval(t, n2, "STRONG", 8)

    r = client.get("/api/shortlist?ai_role=ai_only")
    assert r.status_code == 200
    items = r.json()
    employers = sorted(i["employer"] for i in items)
    assert employers == ["AI Co A", "AI Co B"]


def test_filter_non_ai_role_only_excludes_postings_with_subtype(harness):
    """Spec 6b TASK 3.5: ai_role=non_ai_only filters to postings
    where ai_subtype IS NULL."""
    client, t = harness
    a1 = _add_posting(t, "AI Co", "ML Engineer", "https://x/a1")
    n1 = _add_posting(t, "Bank", "Senior PM", "https://x/n1")
    n2 = _add_posting(t, "Telco", "Delivery Lead", "https://x/n2")
    _set_ai_subtype(t, a1, "ai_engineer")
    _seed_eval(t, a1, "TOP_TIER", 9)
    _seed_eval(t, n1, "TOP_TIER", 9)
    _seed_eval(t, n2, "STRONG", 8)

    r = client.get("/api/shortlist?ai_role=non_ai_only")
    assert r.status_code == 200
    items = r.json()
    employers = sorted(i["employer"] for i in items)
    assert employers == ["Bank", "Telco"]


def test_filter_ai_role_default_returns_all(harness):
    """Spec 6b TASK 3.5: omitting ai_role returns the full shortlist
    (no AI-related filter applied)."""
    client, t = harness
    a1 = _add_posting(t, "AI Co", "ML Engineer", "https://x/a1")
    n1 = _add_posting(t, "Bank", "Senior PM", "https://x/n1")
    _set_ai_subtype(t, a1, "ai_engineer")
    _seed_eval(t, a1, "TOP_TIER", 9)
    _seed_eval(t, n1, "TOP_TIER", 9)

    r = client.get("/api/shortlist")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 2


def test_unselect_inverts_select_and_logs_both_transitions(harness):
    """Spec 6b TASK 4: per-card 'Select to apply' button now toggles
    off via /unselect. Verifies the inverse-of-select side effects and
    that both status transitions land in the events table."""
    client, t = harness
    oid = _add_posting(t, "TD", "PM", "https://x/1")
    _seed_eval(t, oid, "TOP_TIER", 9)

    # Select establishes: opp.status='shortlisted', application row
    # exists with selected_at populated.
    sel = client.post(f"/api/shortlist/{oid}/select").json()
    app_id = sel["application_id"]
    assert t.get_opportunity_by_id(oid)["status"] == "shortlisted"
    assert t.get_application_by_id(app_id)["selected_at"] is not None

    # Unselect inverts: opp.status='new', application row preserved
    # but selected_at cleared to NULL. Body returns the new status.
    r = client.post(f"/api/shortlist/{oid}/unselect")
    assert r.status_code == 200
    assert r.json()["status"] == "new"
    assert t.get_opportunity_by_id(oid)["status"] == "new"
    app = t.get_application_by_id(app_id)
    assert app is not None, "application row should be preserved"
    assert app["selected_at"] is None, "selected_at should be cleared"

    # Event log captures both transitions: opportunity_classified
    # (set by update_opportunity_status when status='shortlisted')
    # and status_changed (set when status='new').
    events = t.list_events_by_entity("opportunity", oid)
    types = [e["event_type"] for e in events]
    assert "opportunity_classified" in types
    assert "status_changed" in types


def test_skip_marks_dismissed_and_excludes_from_shortlist(harness):
    client, t = harness
    oid = _add_posting(t, "TD", "PM", "https://x/1")
    _seed_eval(t, oid, "TOP_TIER", 9)

    r = client.post(f"/api/shortlist/{oid}/skip")
    assert r.status_code == 200
    assert t.get_opportunity_by_id(oid)["status"] == "dismissed"
    assert client.get("/api/shortlist").json() == []


def test_skip_404_on_unknown_posting(harness):
    client, _ = harness
    r = client.post("/api/shortlist/999/skip")
    assert r.status_code == 404


def test_unskip_resets_opportunity_status(harness):
    client, t = harness
    oid = _add_posting(t, "TD", "PM", "https://x/1")
    _seed_eval(t, oid, "TOP_TIER", 9)
    client.post(f"/api/shortlist/{oid}/skip")
    assert t.get_opportunity_by_id(oid)["status"] == "dismissed"

    r = client.post(f"/api/shortlist/{oid}/unskip")
    assert r.status_code == 200
    assert t.get_opportunity_by_id(oid)["status"] == "new"


def test_unskip_allows_subsequent_select(harness):
    client, t = harness
    oid = _add_posting(t, "TD", "PM", "https://x/1")
    _seed_eval(t, oid, "TOP_TIER", 9)
    client.post(f"/api/shortlist/{oid}/skip")
    client.post(f"/api/shortlist/{oid}/unskip")

    r = client.post(f"/api/shortlist/{oid}/select")
    assert r.status_code == 200
    assert t.get_opportunity_by_id(oid)["status"] == "shortlisted"


def test_skip_and_unskip_roundtrip(harness):
    client, t = harness
    oid = _add_posting(t, "TD", "PM", "https://x/1")
    _seed_eval(t, oid, "TOP_TIER", 9)

    client.post(f"/api/shortlist/{oid}/skip")
    assert client.get("/api/shortlist").json() == []

    client.post(f"/api/shortlist/{oid}/unskip")
    items = client.get("/api/shortlist").json()
    assert len(items) == 1
    assert items[0]["opportunity_id"] == oid


def test_unskip_404_on_unknown_posting(harness):
    client, _ = harness
    r = client.post("/api/shortlist/999/unskip")
    assert r.status_code == 404


def _add_posting_with_location(tracker, employer, title, source_url, location):
    cid = tracker.upsert_company(employer)
    opp_id, _ = tracker.insert_opportunity(
        company_id=cid, source="manual_entry",
        source_url=source_url, title=title,
        location=location, posting_text="Lead delivery.",
    )
    return opp_id


def test_shortlist_city_filter_gta_matches_toronto(harness):
    client, t = harness
    oid_tor = _add_posting_with_location(
        t, "TD", "PM", "https://x/1", "Toronto, ON"
    )
    oid_yyc = _add_posting_with_location(
        t, "BMO Calgary", "PM", "https://x/2", "Calgary, AB"
    )
    _seed_eval(t, oid_tor, "TOP_TIER", 9)
    _seed_eval(t, oid_yyc, "TOP_TIER", 9)

    items = client.get("/api/shortlist?city=gta").json()
    assert [i["opportunity_id"] for i in items] == [oid_tor]


def test_shortlist_city_filter_calgary(harness):
    client, t = harness
    oid_tor = _add_posting_with_location(
        t, "TD", "PM", "https://x/1", "Toronto, ON"
    )
    oid_yyc = _add_posting_with_location(
        t, "BMO Calgary", "PM", "https://x/2", "Calgary, AB"
    )
    _seed_eval(t, oid_tor, "TOP_TIER", 9)
    _seed_eval(t, oid_yyc, "TOP_TIER", 9)

    items = client.get("/api/shortlist?city=calgary").json()
    assert [i["opportunity_id"] for i in items] == [oid_yyc]


def test_shortlist_city_filter_returns_all_when_unset(harness):
    client, t = harness
    oid_tor = _add_posting_with_location(
        t, "TD", "PM", "https://x/1", "Toronto, ON"
    )
    oid_yyc = _add_posting_with_location(
        t, "BMO Calgary", "PM", "https://x/2", "Calgary, AB"
    )
    _seed_eval(t, oid_tor, "TOP_TIER", 9)
    _seed_eval(t, oid_yyc, "TOP_TIER", 9)

    items = client.get("/api/shortlist").json()
    assert {i["opportunity_id"] for i in items} == {oid_tor, oid_yyc}
