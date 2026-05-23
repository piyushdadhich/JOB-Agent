"""Spec 13 TASK 6 — CSV export endpoint tests."""
from __future__ import annotations

import csv
import io
import json

import pytest
from fastapi.testclient import TestClient

from dashboard.backend import app as app_module
from dashboard.backend.deps import get_tracker
from engine.persistence.tracker import Tracker


@pytest.fixture
def client(tmp_path):
    db = tmp_path / "t.db"
    t = Tracker(profile_id="default", db_path=db)

    cid = t.upsert_company("Acme Corp")
    opp_top, _ = t.insert_opportunity(
        company_id=cid, source="t",
        source_url="https://example.com/top",
        title="Senior PM", location="Toronto",
    )
    opp_strong, _ = t.insert_opportunity(
        company_id=cid, source="t",
        source_url="https://example.com/strong",
        title="PM", location="Calgary",
    )
    opp_skip, _ = t.insert_opportunity(
        company_id=cid, source="t",
        source_url="https://example.com/skip",
        title="Junior PM", location="Remote",
    )

    # Decisions: TOP_TIER + STRONG + SKIP.
    for opp_id, tier, score in [
        (opp_top, "TOP_TIER", 8),
        (opp_strong, "STRONG", 6),
        (opp_skip, "SKIP", 1),
    ]:
        t._execute(
            "INSERT INTO eval_decisions "
            "(opportunity_id, evaluator_version, tier, fit_score, "
            " stage_trace, evaluated_at) "
            "VALUES (?, 'v1', ?, ?, '[]', '2026-01-01T00:00:00+00:00')",
            (opp_id, tier, score),
        )

    # match_scores + skill_labels for the TOP_TIER row.
    t._execute(
        "INSERT INTO match_scores "
        "(opportunity_id, scorer_version, overlap_count, "
        " posting_skill_count, inventory_skill_count, "
        " coverage_raw, coverage_idf, overlap_skill_ids, "
        " missed_skill_ids, bucket, scored_at) "
        "VALUES (?, 'v1', 1, 3, 100, 0.33, 0.4, ?, '[]', 'high', "
        " '2026-01-01T00:00:00+00:00')",
        (opp_top, json.dumps(["KS_AGILE"])),
    )
    t._execute(
        "INSERT INTO skill_labels (skill_id, label, taxonomy) "
        "VALUES ('KS_AGILE', 'Agile', 'lightcast')",
    )
    t.close()

    def _ovr():
        tt = Tracker(profile_id="default", db_path=db)
        try:
            yield tt
        finally:
            tt.close()

    app_module.FRONTEND_DIST = tmp_path / "nope"
    app = app_module.create_app()
    app.dependency_overrides[get_tracker] = _ovr
    return TestClient(app)


def _parse_csv(body: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(body)))


def test_export_default_returns_top_and_strong_only(client):
    r = client.get("/api/shortlist/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "shortlist.csv" in r.headers["content-disposition"]
    rows = _parse_csv(r.text)
    tiers = {row["tier"] for row in rows}
    assert tiers == {"TOP_TIER", "STRONG"}


def test_export_includes_all_when_flag_set(client):
    r = client.get("/api/shortlist/export?include_all_tiers=true")
    rows = _parse_csv(r.text)
    tiers = {row["tier"] for row in rows}
    assert "SKIP" in tiers


def test_export_resolves_matched_skill_labels(client):
    r = client.get("/api/shortlist/export")
    rows = _parse_csv(r.text)
    top = next(r for r in rows if r["tier"] == "TOP_TIER")
    # KS_AGILE → "Agile" via skill_labels.
    assert "Agile" in top["matched_skills"]


def test_export_csv_includes_required_columns(client):
    r = client.get("/api/shortlist/export")
    rows = _parse_csv(r.text)
    assert rows
    required = {
        "employer", "title", "tier", "fit_score", "location",
        "matched_skills", "posting_url", "date_discovered", "status",
    }
    assert required.issubset(rows[0].keys())
