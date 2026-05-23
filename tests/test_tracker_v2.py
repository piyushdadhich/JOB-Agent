"""Tests for engine.persistence.tracker.Tracker (v2 schema)."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.persistence.tracker import (
    InvalidStatusError,
    Tracker,
    TrackerError,
    normalize_employer_name,
)


@pytest.fixture
def tmp_tracker(tmp_path):
    db = tmp_path / "tracker.db"
    t = Tracker(profile_id="test", db_path=db)
    yield t
    t.close()


# ----- Constructor / bootstrap --------------------------------------------

def test_constructor_requires_profile_id(tmp_path):
    with pytest.raises(TrackerError):
        Tracker(profile_id="")


def test_default_path_is_data_profile_id_tracker_db(monkeypatch, tmp_path):
    # Redirect default project root by passing db_path explicitly to assert
    # the convention is "data/{profile_id}/tracker.db" relative to project root.
    expected = PROJECT_ROOT / "data" / "alice_unit_test" / "tracker.db"
    if expected.exists():
        os.remove(expected)
    if expected.parent.exists() and not any(expected.parent.iterdir()):
        expected.parent.rmdir()
    t = Tracker(profile_id="alice_unit_test")
    try:
        assert Path(t.db_path) == expected
        assert expected.exists()
    finally:
        t.close()
        if expected.exists():
            os.remove(expected)
        if expected.parent.exists() and not any(expected.parent.iterdir()):
            expected.parent.rmdir()


def test_creates_profile_directory_if_missing(tmp_path):
    target = tmp_path / "deep" / "nested" / "tracker.db"
    t = Tracker(profile_id="x", db_path=target)
    try:
        assert target.exists()
    finally:
        t.close()


def test_runs_schema_on_first_use(tmp_tracker):
    assert tmp_tracker.schema_version() >= 26


def test_does_not_rerun_schema_on_existing_db(tmp_path):
    db = tmp_path / "t.db"
    t1 = Tracker(profile_id="p", db_path=db)
    cid = t1.upsert_company(name="Acme")
    t1.close()

    t2 = Tracker(profile_id="p", db_path=db)
    try:
        # Schema not re-run = data preserved.
        assert t2.get_company_by_id(cid) is not None
        assert t2.schema_version() >= 26
    finally:
        t2.close()


def test_no_project_root_env_required(monkeypatch, tmp_path):
    # Remove PROJECT_ROOT if any consumer set it; v2 must not require it.
    monkeypatch.delenv("PROJECT_ROOT", raising=False)
    db = tmp_path / "t.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert t.schema_version() >= 26
    finally:
        t.close()


# ----- Companies ----------------------------------------------------------

def test_upsert_company_creates_new_returns_id(tmp_tracker):
    cid = tmp_tracker.upsert_company(name="Acme Corp")
    assert isinstance(cid, int) and cid > 0


def test_upsert_company_idempotent_on_normalized_name(tmp_tracker):
    a = tmp_tracker.upsert_company(name="Acme Corp")
    b = tmp_tracker.upsert_company(name="  ACME  Corp ")
    assert a == b
    assert tmp_tracker.count_companies() == 1


def test_upsert_company_updates_last_seen_at(tmp_tracker):
    a = tmp_tracker.upsert_company(name="Acme")
    first = tmp_tracker.get_company_by_id(a)["last_seen_at"]
    time.sleep(0.01)
    tmp_tracker.upsert_company(name="Acme")
    second = tmp_tracker.get_company_by_id(a)["last_seen_at"]
    assert second >= first


def test_upsert_company_does_not_clobber_industry_with_null(tmp_tracker):
    cid = tmp_tracker.upsert_company(name="Acme", industry="Finance")
    tmp_tracker.upsert_company(name="Acme", industry=None)
    assert tmp_tracker.get_company_by_id(cid)["industry"] == "Finance"


def test_normalize_employer_name_lowercases_and_strips():
    assert normalize_employer_name("  Acme Corp  ") == "acme corp"


def test_normalize_employer_name_collapses_whitespace():
    assert normalize_employer_name("Acme   Corp\tInc") == "acme corp inc"


# ----- Opportunities ------------------------------------------------------

def test_insert_opportunity_returns_was_new_true(tmp_tracker):
    cid = tmp_tracker.upsert_company(name="Acme")
    opp_id, was_new = tmp_tracker.insert_opportunity(
        company_id=cid, source="jobspy",
        source_url="https://x.com/jobs/1", title="PM",
    )
    assert was_new is True
    assert opp_id > 0


def test_insert_opportunity_returns_was_new_false_on_duplicate_url(tmp_tracker):
    cid = tmp_tracker.upsert_company(name="Acme")
    opp_id1, _ = tmp_tracker.insert_opportunity(
        company_id=cid, source="jobspy",
        source_url="https://x.com/jobs/1", title="PM",
    )
    opp_id2, was_new = tmp_tracker.insert_opportunity(
        company_id=cid, source="jobspy",
        source_url="https://x.com/jobs/1", title="PM",
    )
    assert opp_id1 == opp_id2
    assert was_new is False


def test_insert_opportunity_normalizes_url_for_dedup(tmp_tracker):
    cid = tmp_tracker.upsert_company(name="Acme")
    a, _ = tmp_tracker.insert_opportunity(
        company_id=cid, source="jobspy",
        source_url="https://x.com/jobs/1/", title="PM",
    )
    b, was_new = tmp_tracker.insert_opportunity(
        company_id=cid, source="jobspy",
        source_url="https://x.com/jobs/1?utm_source=lk&fbclid=z",
        title="PM",
    )
    assert a == b
    assert was_new is False


def test_insert_opportunity_updates_last_seen_at_on_duplicate(tmp_tracker):
    cid = tmp_tracker.upsert_company(name="Acme")
    opp_id, _ = tmp_tracker.insert_opportunity(
        company_id=cid, source="jobspy",
        source_url="https://x.com/jobs/1", title="PM",
    )
    first = tmp_tracker.get_opportunity_by_id(opp_id)["last_seen_at"]
    time.sleep(0.01)
    tmp_tracker.insert_opportunity(
        company_id=cid, source="jobspy",
        source_url="https://x.com/jobs/1", title="PM",
    )
    second = tmp_tracker.get_opportunity_by_id(opp_id)["last_seen_at"]
    assert second >= first


def test_insert_opportunity_serializes_raw_payload_to_json(tmp_tracker):
    cid = tmp_tracker.upsert_company(name="Acme")
    payload = {"foo": "bar", "n": 42}
    opp_id, _ = tmp_tracker.insert_opportunity(
        company_id=cid, source="jobspy",
        source_url="https://x.com/jobs/1", title="PM",
        raw_payload=payload,
    )
    row = tmp_tracker.get_opportunity_by_id(opp_id)
    assert json.loads(row["raw_payload"]) == payload


def test_insert_opportunity_serializes_search_context_to_json(tmp_tracker):
    cid = tmp_tracker.upsert_company(name="Acme")
    ctx = {"city": "toronto", "role_type": "delivery_manager"}
    opp_id, _ = tmp_tracker.insert_opportunity(
        company_id=cid, source="jobspy",
        source_url="https://x.com/jobs/1", title="PM",
        search_context=ctx,
    )
    row = tmp_tracker.get_opportunity_by_id(opp_id)
    assert json.loads(row["search_context"]) == ctx


def test_get_opportunity_by_id_returns_employer_alias(tmp_tracker):
    cid = tmp_tracker.upsert_company(name="Acme Corp")
    opp_id, _ = tmp_tracker.insert_opportunity(
        company_id=cid, source="jobspy",
        source_url="https://x/1", title="PM",
    )
    row = tmp_tracker.get_opportunity_by_id(opp_id)
    assert row["employer"] == "Acme Corp"
    assert row["url"] == "https://x/1"


# ----- Evaluations / fit ---------------------------------------------------

def test_record_evaluation_inserts_row(tmp_tracker):
    cid = tmp_tracker.upsert_company(name="Acme")
    opp_id, _ = tmp_tracker.insert_opportunity(
        company_id=cid, source="jobspy",
        source_url="https://x/1", title="PM",
    )
    eval_id = tmp_tracker.record_evaluation(
        opportunity_id=opp_id, evaluator_version="v0",
        tier="STRONG", fit_score=8, sector="financial_services",
        role_type="delivery_manager",
        stage_trace={"stage1": "ok"},
        reasoning="strong PM background",
    )
    assert eval_id > 0
    latest = tmp_tracker.get_latest_evaluation(opp_id)
    assert latest["fit_score"] == 8
    assert latest["tier"] == "STRONG"


def test_update_opportunity_fit_writes_eval_decision(tmp_tracker):
    cid = tmp_tracker.upsert_company(name="Acme")
    opp_id, _ = tmp_tracker.insert_opportunity(
        company_id=cid, source="jobspy",
        source_url="https://x/1", title="PM",
    )
    tmp_tracker.update_opportunity_fit(
        opp_id, fit_score=7, fit_reasoning="ok"
    )
    row = tmp_tracker.get_opportunity_by_id(opp_id)
    assert row["fit_score"] == 7
    assert row["fit_reasoning"] == "ok"


def test_invalid_tier_raises(tmp_tracker):
    cid = tmp_tracker.upsert_company(name="Acme")
    opp_id, _ = tmp_tracker.insert_opportunity(
        company_id=cid, source="jobspy",
        source_url="https://x/1", title="PM",
    )
    with pytest.raises(InvalidStatusError):
        tmp_tracker.record_evaluation(
            opportunity_id=opp_id, evaluator_version="v0",
            tier="BOGUS", fit_score=5, sector=None,
            role_type=None, stage_trace={}, reasoning=None,
        )


# ----- Status updates / events --------------------------------------------

def test_update_opportunity_status_logs_event(tmp_tracker):
    cid = tmp_tracker.upsert_company(name="Acme")
    opp_id, _ = tmp_tracker.insert_opportunity(
        company_id=cid, source="jobspy",
        source_url="https://x/1", title="PM",
    )
    tmp_tracker.update_opportunity_status(opp_id, "shortlisted")
    row = tmp_tracker.get_opportunity_by_id(opp_id)
    assert row["status"] == "shortlisted"
    events = tmp_tracker.list_events_by_entity("opportunity", opp_id)
    assert any(e["event_type"] == "opportunity_classified" for e in events)


def test_log_event_records_opportunity_discovered(tmp_tracker):
    cid = tmp_tracker.upsert_company(name="Acme")
    tmp_tracker.insert_opportunity(
        company_id=cid, source="jobspy",
        source_url="https://x/1", title="PM",
    )
    discovered = tmp_tracker.list_events_by_type("opportunity_discovered")
    assert len(discovered) == 1


# ----- Profile skills / extracted skill IDs (v2.2) ------------------------

def test_upsert_profile_skills_inserts_new_row(tmp_tracker):
    tmp_tracker.upsert_profile_skills(
        profile_id="default", taxonomy="esco",
        skill_ids=["a", "b"], source_doc="x.md",
    )
    row = tmp_tracker.get_profile_skills("default", "esco")
    assert row is not None
    assert row["skill_ids"] == ["a", "b"]
    assert row["taxonomy"] == "esco"
    assert row["source_doc"] == "x.md"


def test_upsert_profile_skills_replaces_existing(tmp_tracker):
    tmp_tracker.upsert_profile_skills(
        profile_id="default", taxonomy="esco",
        skill_ids=["a"], source_doc="x.md",
    )
    tmp_tracker.upsert_profile_skills(
        profile_id="default", taxonomy="esco",
        skill_ids=["c", "d", "e"], source_doc="x.md",
    )
    row = tmp_tracker.get_profile_skills("default", "esco")
    assert row["skill_ids"] == ["c", "d", "e"]


def test_get_profile_skills_returns_none_for_missing(tmp_tracker):
    assert tmp_tracker.get_profile_skills("default", "esco") is None


def test_get_profile_skills_roundtrips_skill_ids_list(tmp_tracker):
    ids = ["urn:esco:1", "urn:esco:2", "S4.8.1"]
    tmp_tracker.upsert_profile_skills(
        profile_id="default", taxonomy="lightcast",
        skill_ids=ids, source_doc="career_inventory.md",
    )
    row = tmp_tracker.get_profile_skills("default", "lightcast")
    assert row["skill_ids"] == ids


def test_update_opportunity_skills_persists_json(tmp_tracker):
    cid = tmp_tracker.upsert_company(name="Acme")
    opp_id, _ = tmp_tracker.insert_opportunity(
        company_id=cid, source="jobspy",
        source_url="https://x/1", title="PM",
    )
    tmp_tracker.update_opportunity_skills(opp_id, ["s1", "s2"])
    row = tmp_tracker._query_one(
        "SELECT extracted_skill_ids FROM opportunities WHERE id = ?",
        (opp_id,),
    )
    assert row["extracted_skill_ids"] is not None
    assert json.loads(row["extracted_skill_ids"]) == ["s1", "s2"]


def test_update_opportunity_skills_roundtrips_list(tmp_tracker):
    cid = tmp_tracker.upsert_company(name="Acme")
    opp_id, _ = tmp_tracker.insert_opportunity(
        company_id=cid, source="jobspy",
        source_url="https://x/1", title="PM",
    )
    expected = ["urn:esco:1", "S4.8.1", "K0413"]
    tmp_tracker.update_opportunity_skills(opp_id, expected)
    row = tmp_tracker._query_one(
        "SELECT extracted_skill_ids FROM opportunities WHERE id = ?",
        (opp_id,),
    )
    assert json.loads(row["extracted_skill_ids"]) == expected


# ----- Dual-taxonomy update_opportunity_skills (v2.3) ---------------------

def test_update_opportunity_skills_dual_persists_primary_and_secondary(tmp_tracker):
    cid = tmp_tracker.upsert_company(name="Acme")
    opp_id, _ = tmp_tracker.insert_opportunity(
        company_id=cid, source="jobspy",
        source_url="https://x/1", title="PM",
    )
    tmp_tracker.update_opportunity_skills_dual(
        opportunity_id=opp_id,
        primary_skill_ids=["LC1", "LC2"],
        secondary_skill_ids=["ESCO1", "ESCO2", "ESCO3"],
        secondary_taxonomy="esco",
    )
    row = tmp_tracker._query_one(
        "SELECT extracted_skill_ids, extracted_skill_ids_secondary "
        "FROM opportunities WHERE id = ?",
        (opp_id,),
    )
    assert json.loads(row["extracted_skill_ids"]) == ["LC1", "LC2"]
    assert json.loads(row["extracted_skill_ids_secondary"]) == [
        "ESCO1", "ESCO2", "ESCO3",
    ]


def test_update_opportunity_skills_dual_persists_taxonomy_label(tmp_tracker):
    cid = tmp_tracker.upsert_company(name="Acme")
    opp_id, _ = tmp_tracker.insert_opportunity(
        company_id=cid, source="jobspy",
        source_url="https://x/1", title="PM",
    )
    tmp_tracker.update_opportunity_skills_dual(
        opportunity_id=opp_id,
        primary_skill_ids=["LC1"],
        secondary_skill_ids=["ESCO1"],
        secondary_taxonomy="esco",
    )
    row = tmp_tracker._query_one(
        "SELECT secondary_taxonomy FROM opportunities WHERE id = ?",
        (opp_id,),
    )
    assert row["secondary_taxonomy"] == "esco"


def test_update_opportunity_skills_dual_overwrites_on_rerun(tmp_tracker):
    cid = tmp_tracker.upsert_company(name="Acme")
    opp_id, _ = tmp_tracker.insert_opportunity(
        company_id=cid, source="jobspy",
        source_url="https://x/1", title="PM",
    )
    tmp_tracker.update_opportunity_skills_dual(
        opportunity_id=opp_id,
        primary_skill_ids=["LC1"],
        secondary_skill_ids=["ESCO1"],
        secondary_taxonomy="esco",
    )
    tmp_tracker.update_opportunity_skills_dual(
        opportunity_id=opp_id,
        primary_skill_ids=["LCx", "LCy"],
        secondary_skill_ids=["ESCOx"],
        secondary_taxonomy="esco",
    )
    row = tmp_tracker._query_one(
        "SELECT extracted_skill_ids, extracted_skill_ids_secondary "
        "FROM opportunities WHERE id = ?",
        (opp_id,),
    )
    assert json.loads(row["extracted_skill_ids"]) == ["LCx", "LCy"]
    assert json.loads(row["extracted_skill_ids_secondary"]) == ["ESCOx"]


def test_update_opportunity_skills_legacy_single_arg_still_works(tmp_tracker):
    cid = tmp_tracker.upsert_company(name="Acme")
    opp_id, _ = tmp_tracker.insert_opportunity(
        company_id=cid, source="jobspy",
        source_url="https://x/1", title="PM",
    )
    tmp_tracker.update_opportunity_skills(opp_id, ["legacy1", "legacy2"])
    row = tmp_tracker._query_one(
        "SELECT extracted_skill_ids, extracted_skill_ids_secondary, "
        "       secondary_taxonomy "
        "FROM opportunities WHERE id = ?",
        (opp_id,),
    )
    assert json.loads(row["extracted_skill_ids"]) == ["legacy1", "legacy2"]
    # Legacy method does not touch secondary columns.
    assert row["extracted_skill_ids_secondary"] is None
    assert row["secondary_taxonomy"] is None


# ----- Match scores + skill labels (v2.4) ---------------------------------

def _make_opp(tracker):
    cid = tracker.upsert_company(name="Acme")
    opp_id, _ = tracker.insert_opportunity(
        company_id=cid, source="jobspy",
        source_url="https://x/1", title="PM",
    )
    return opp_id


def test_insert_match_score_persists_deterministic_fields(tmp_tracker):
    opp_id = _make_opp(tmp_tracker)
    tmp_tracker.insert_match_score(
        opportunity_id=opp_id,
        scorer_version="v1",
        overlap_count=5,
        posting_skill_count=20,
        inventory_skill_count=140,
        coverage_raw=0.25,
        coverage_idf=0.30,
        overlap_skill_ids=["a", "b", "c", "d", "e"],
        missed_skill_ids=["f", "g"],
        bucket="high",
    )
    row = tmp_tracker.get_match_score(opp_id, "v1")
    assert row["overlap_count"] == 5
    assert row["posting_skill_count"] == 20
    assert row["inventory_skill_count"] == 140
    assert row["coverage_raw"] == 0.25
    assert row["coverage_idf"] == 0.30
    assert row["overlap_skill_ids"] == ["a", "b", "c", "d", "e"]
    assert row["missed_skill_ids"] == ["f", "g"]
    assert row["bucket"] == "high"
    assert row["scored_at"] is not None
    # Gemma fields all NULL.
    assert row["gemma_score"] is None
    assert row["gemma_summary"] is None


def test_update_match_score_gemma_persists_gemma_fields(tmp_tracker):
    opp_id = _make_opp(tmp_tracker)
    tmp_tracker.insert_match_score(
        opportunity_id=opp_id, scorer_version="v1",
        overlap_count=5, posting_skill_count=20,
        inventory_skill_count=140,
        coverage_raw=0.25, coverage_idf=0.30,
        overlap_skill_ids=["a"], missed_skill_ids=[],
        bucket="high",
    )
    tmp_tracker.update_match_score_gemma(
        opportunity_id=opp_id, scorer_version="v1",
        gemma_score=8,
        gemma_top_matches=[{"posting_skill": "Java", "reason": "x"}],
        gemma_transferable=[{"posting_skill": "Kotlin",
                             "bridged_from": "Java",
                             "reason": "JVM family"}],
        gemma_critical_gaps=["Rust"],
        gemma_summary="Strong fit overall.",
        gemma_hallucination_flags=0,
        gemma_raw_response='{"score": 8}',
    )
    row = tmp_tracker.get_match_score(opp_id, "v1")
    assert row["gemma_score"] == 8
    assert row["gemma_top_matches"][0]["posting_skill"] == "Java"
    assert row["gemma_critical_gaps"] == ["Rust"]
    assert row["gemma_summary"] == "Strong fit overall."
    assert row["gemma_hallucination_flags"] == 0
    # Deterministic fields preserved.
    assert row["overlap_count"] == 5


def test_get_match_score_returns_none_for_missing(tmp_tracker):
    assert tmp_tracker.get_match_score(999, "v1") is None


def test_upsert_skill_label_inserts_and_updates(tmp_tracker):
    tmp_tracker.upsert_skill_label(
        "S1", "Software Engineering", "lightcast", "skill",
    )
    labels = tmp_tracker.get_skill_labels(["S1"])
    assert labels == {"S1": "Software Engineering"}
    # Upsert again with changed label -> updates.
    tmp_tracker.upsert_skill_label(
        "S1", "Software Development", "lightcast", "skill",
    )
    labels = tmp_tracker.get_skill_labels(["S1"])
    assert labels == {"S1": "Software Development"}


def test_get_skill_labels_returns_unknown_for_missing(tmp_tracker):
    tmp_tracker.upsert_skill_label("S1", "Java", "lightcast")
    labels = tmp_tracker.get_skill_labels(["S1", "S2", "S3"])
    assert labels["S1"] == "Java"
    assert labels["S2"] == "<unknown>"
    assert labels["S3"] == "<unknown>"


def test_list_match_scores_filters_by_bucket(tmp_tracker):
    cid = tmp_tracker.upsert_company(name="Acme")
    a, _ = tmp_tracker.insert_opportunity(
        company_id=cid, source="x", source_url="https://x/1", title="A",
    )
    b, _ = tmp_tracker.insert_opportunity(
        company_id=cid, source="x", source_url="https://x/2", title="B",
    )
    c, _ = tmp_tracker.insert_opportunity(
        company_id=cid, source="x", source_url="https://x/3", title="C",
    )
    tmp_tracker.insert_match_score(
        opportunity_id=a, scorer_version="v1",
        overlap_count=0, posting_skill_count=10,
        inventory_skill_count=140,
        coverage_raw=0.0, coverage_idf=0.0,
        overlap_skill_ids=[], missed_skill_ids=[],
        bucket="zero",
    )
    tmp_tracker.insert_match_score(
        opportunity_id=b, scorer_version="v1",
        overlap_count=2, posting_skill_count=10,
        inventory_skill_count=140,
        coverage_raw=0.2, coverage_idf=0.15,
        overlap_skill_ids=["x", "y"], missed_skill_ids=[],
        bucket="low",
    )
    tmp_tracker.insert_match_score(
        opportunity_id=c, scorer_version="v1",
        overlap_count=8, posting_skill_count=10,
        inventory_skill_count=140,
        coverage_raw=0.8, coverage_idf=0.75,
        overlap_skill_ids=["x"] * 8, missed_skill_ids=[],
        bucket="high",
    )
    high = tmp_tracker.list_match_scores("v1", bucket="high")
    assert len(high) == 1
    assert high[0]["opportunity_id"] == c
    all_rows = tmp_tracker.list_match_scores("v1")
    assert len(all_rows) == 3
    # Default order: coverage_idf DESC -> c, b, a
    assert [r["opportunity_id"] for r in all_rows] == [c, b, a]


# ----- Eval labels (v2.5) ------------------------------------------------

def test_insert_eval_label_persists_verdict_and_metadata(tmp_tracker):
    opp_id = _make_opp(tmp_tracker)
    tmp_tracker.insert_eval_label(
        opportunity_id=opp_id, verdict="shortlist",
        reason="strong_PM", notes="great fit",
    )
    row = tmp_tracker.get_eval_label(opp_id)
    assert row["verdict"] == "shortlist"
    assert row["reason"] == "strong_PM"
    assert row["notes"] == "great fit"
    assert row["labeled_by"] == "default"
    assert row["labeled_at"] is not None


def test_insert_eval_label_replaces_on_re_insert(tmp_tracker):
    opp_id = _make_opp(tmp_tracker)
    tmp_tracker.insert_eval_label(opp_id, "skip")
    tmp_tracker.insert_eval_label(opp_id, "shortlist")
    row = tmp_tracker.get_eval_label(opp_id)
    assert row["verdict"] == "shortlist"


def test_insert_eval_label_rejects_invalid_verdict(tmp_tracker):
    opp_id = _make_opp(tmp_tracker)
    with pytest.raises(Exception):
        tmp_tracker.insert_eval_label(opp_id, "bogus")


def test_list_eval_labels_filters_by_verdict(tmp_tracker):
    cid = tmp_tracker.upsert_company(name="Acme")
    a, _ = tmp_tracker.insert_opportunity(
        company_id=cid, source="x", source_url="https://x/1", title="A",
    )
    b, _ = tmp_tracker.insert_opportunity(
        company_id=cid, source="x", source_url="https://x/2", title="B",
    )
    c, _ = tmp_tracker.insert_opportunity(
        company_id=cid, source="x", source_url="https://x/3", title="C",
    )
    tmp_tracker.insert_eval_label(a, "shortlist")
    tmp_tracker.insert_eval_label(b, "skip")
    tmp_tracker.insert_eval_label(c, "skip")
    shortlists = tmp_tracker.list_eval_labels(verdict="shortlist")
    assert len(shortlists) == 1
    assert shortlists[0]["opportunity_id"] == a
    all_rows = tmp_tracker.list_eval_labels()
    assert len(all_rows) == 3


# ----- Stage decisions (v2.6) --------------------------------------------

def test_insert_stage_decision_persists_basic_fields(tmp_tracker):
    opp_id = _make_opp(tmp_tracker)
    tmp_tracker.insert_stage_decision(
        opportunity_id=opp_id,
        stage_name="stage1",
        stage_version="stage1-v1",
        decision="PASS",
        reason="passed all filters",
    )
    row = tmp_tracker.get_stage_decision(
        opp_id, "stage1", "stage1-v1"
    )
    assert row["decision"] == "PASS"
    assert row["reason"] == "passed all filters"
    assert row["decided_at"] is not None


def test_insert_stage_decision_persists_metadata_dict(tmp_tracker):
    opp_id = _make_opp(tmp_tracker)
    tmp_tracker.insert_stage_decision(
        opportunity_id=opp_id,
        stage_name="stage2",
        stage_version="stage2-v1",
        decision="UNCERTAIN_PASS",
        reason="confidence below threshold",
        metadata={
            "role_type": "delivery_manager",
            "confidence": 0.62,
            "primary_function": "Senior Project Manager",
        },
    )
    row = tmp_tracker.get_stage_decision(
        opp_id, "stage2", "stage2-v1"
    )
    assert isinstance(row["metadata"], dict)
    assert row["metadata"]["confidence"] == 0.62
    assert row["metadata"]["role_type"] == "delivery_manager"


def test_insert_stage_decision_replaces_on_re_insert(tmp_tracker):
    opp_id = _make_opp(tmp_tracker)
    tmp_tracker.insert_stage_decision(
        opp_id, "stage1", "stage1-v1", "PASS",
    )
    tmp_tracker.insert_stage_decision(
        opp_id, "stage1", "stage1-v1", "EXCLUDED",
        reason="employer added to exclusions",
    )
    row = tmp_tracker.get_stage_decision(
        opp_id, "stage1", "stage1-v1"
    )
    assert row["decision"] == "EXCLUDED"
    assert row["reason"] == "employer added to exclusions"


def test_get_stage_decision_returns_none_for_missing(tmp_tracker):
    assert tmp_tracker.get_stage_decision(
        999, "stage1", "stage1-v1"
    ) is None


def test_list_stage_decisions_filters_by_stage_name(tmp_tracker):
    cid = tmp_tracker.upsert_company(name="Acme")
    a, _ = tmp_tracker.insert_opportunity(
        company_id=cid, source="x", source_url="https://x/1", title="A",
    )
    b, _ = tmp_tracker.insert_opportunity(
        company_id=cid, source="x", source_url="https://x/2", title="B",
    )
    tmp_tracker.insert_stage_decision(
        a, "stage1", "stage1-v1", "PASS",
    )
    tmp_tracker.insert_stage_decision(
        b, "stage1", "stage1-v1", "EXCLUDED",
    )
    tmp_tracker.insert_stage_decision(
        a, "stage2", "stage2-v1", "PASS",
    )
    s1 = tmp_tracker.list_stage_decisions(stage_name="stage1")
    assert len(s1) == 2
    s2 = tmp_tracker.list_stage_decisions(stage_name="stage2")
    assert len(s2) == 1


def test_list_stage_decisions_filters_by_decision(tmp_tracker):
    cid = tmp_tracker.upsert_company(name="Acme")
    a, _ = tmp_tracker.insert_opportunity(
        company_id=cid, source="x", source_url="https://x/1", title="A",
    )
    b, _ = tmp_tracker.insert_opportunity(
        company_id=cid, source="x", source_url="https://x/2", title="B",
    )
    tmp_tracker.insert_stage_decision(
        a, "stage1", "stage1-v1", "PASS",
    )
    tmp_tracker.insert_stage_decision(
        b, "stage1", "stage1-v1", "EXCLUDED",
    )
    excluded = tmp_tracker.list_stage_decisions(
        stage_name="stage1", decision="EXCLUDED",
    )
    assert len(excluded) == 1
    assert excluded[0]["opportunity_id"] == b
