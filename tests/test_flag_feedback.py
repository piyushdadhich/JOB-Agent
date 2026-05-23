"""Tests for the v2.12 flag-as-bad-match feedback loop.

Covers:
  * FlagRule.matches pattern semantics (no DB)
  * Schema v2.12 migration + columns
  * Flag endpoint (eval_label + decision update + optional rule),
    including transaction rollback
  * persist_record auto-skip at evaluator_version='rule-skip-v1'
  * find_postings_needing_eval treats rule-skip-v1 as current
  * FewShotSelector prefers user_flagged SKIPs
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from dashboard.backend.app import create_app  # noqa: E402
from dashboard.backend.deps import get_tracker  # noqa: E402
from engine.discovery.base import OpportunityRecord  # noqa: E402
from engine.persistence.flag_rules import FlagRule, rule_from_row  # noqa: E402
from engine.persistence.opportunities import persist_record  # noqa: E402
from engine.persistence.tracker import Tracker, TrackerError  # noqa: E402


# --- Pattern matcher tests (no DB) -------------------------------

def test_employer_pattern_wildcard_anchored():
    r = FlagRule(id=1, employer_pattern="Acme*")
    assert r.matches(employer="Acme Corp", title="x")
    assert not r.matches(employer="Foo Acme", title="x")


def test_function_pattern_exact_case_sensitive():
    r = FlagRule(id=1, function_pattern="sales_engineer")
    assert r.matches(
        employer="x", title="y", function="sales_engineer",
    )
    assert not r.matches(
        employer="x", title="y", function="Sales_Engineer",
    )
    assert not r.matches(
        employer="x", title="y", function="sales_eng",
    )


def test_all_null_has_any_pattern_false():
    r = FlagRule(id=1)
    assert r.has_any_pattern() is False


def test_compound_employer_plus_ai_subtype_match():
    r = FlagRule(
        id=1, employer_pattern="acme*", ai_subtype_pattern="ai_engineer",
    )
    assert r.matches(
        employer="Acme Corp", title="x", ai_subtype="ai_engineer",
    )
    assert not r.matches(
        employer="Acme Corp", title="x", ai_subtype="ai_pm",
    )
    assert not r.matches(
        employer="Other", title="x", ai_subtype="ai_engineer",
    )


def test_industry_pattern_exact_match():
    r = FlagRule(id=1, industry_pattern="banking")
    assert r.matches(employer="x", title="y", industry="banking")
    assert not r.matches(employer="x", title="y", industry="Banking")
    assert not r.matches(employer="x", title="y", industry=None)


# --- Schema migration --------------------------------------------

def test_schema_v2_12_columns_and_table(tmp_path):
    db = tmp_path / "t.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert t.schema_version() >= 212
        cols = [
            r[1] for r in t._conn.execute(
                "PRAGMA table_info(eval_decisions)"
            ).fetchall()
        ]
        assert "flagged_at" in cols
        tables = [
            r[0] for r in t._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='flag_rules'"
            ).fetchall()
        ]
        assert tables == ["flag_rules"]
    finally:
        t.close()


# --- Fixtures ----------------------------------------------------

@pytest.fixture
def harness(tmp_path):
    db = tmp_path / "t.db"
    tracker = Tracker(profile_id="p", db_path=db)
    app = create_app()

    def _override():
        yield tracker

    app.dependency_overrides[get_tracker] = _override
    yield TestClient(app), tracker
    tracker.close()


def _add(t, employer, title, url, location="Toronto"):
    cid = t.upsert_company(employer)
    opp_id, _ = t.insert_opportunity(
        company_id=cid, source="manual_entry",
        source_url=url, title=title,
        location=location, posting_text="x",
    )
    return opp_id


def _seed_eval(
    tracker, opp_id, tier="TOP_TIER", *,
    evaluator_version="test", fit_score=8,
):
    now = datetime.now(timezone.utc).isoformat()
    tracker._execute(
        "INSERT INTO eval_decisions "
        "(opportunity_id, evaluator_version, tier, fit_score, "
        " stage_trace, reasoning, evaluated_at) "
        "VALUES (?, ?, ?, ?, '[]', 'good', ?)",
        (opp_id, evaluator_version, tier, fit_score, now),
    )


# --- Flag endpoint -----------------------------------------------

def test_flag_without_rule(harness):
    client, t = harness
    a = _add(t, "Acme", "PM", "https://x/1")
    _seed_eval(t, a, "TOP_TIER", evaluator_version="gemma4-cloud-v1")

    r = client.post(
        f"/api/shortlist/{a}/flag",
        json={"create_rule": False, "rule": None},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["opportunity_id"] == a
    assert body["rule_id"] is None

    # eval_label inserted
    label = t._query_one(
        "SELECT verdict, reason FROM eval_labels WHERE opportunity_id = ?",
        (a,),
    )
    assert label["verdict"] == "skip"
    assert label["reason"] == "user_flagged"

    # latest eval_decision updated to SKIP + flagged_at set
    dec = t._query_one(
        "SELECT tier, flagged_at FROM eval_decisions "
        "WHERE opportunity_id = ? ORDER BY id DESC LIMIT 1",
        (a,),
    )
    assert dec["tier"] == "SKIP"
    assert dec["flagged_at"] is not None

    # No flag_rules row.
    n = t._query_one("SELECT COUNT(*) AS n FROM flag_rules")["n"]
    assert n == 0


def test_flag_with_rule_inserts_all_three(harness):
    client, t = harness
    a = _add(t, "Acme", "Sales Engineer", "https://x/1")
    _seed_eval(t, a, "TOP_TIER", evaluator_version="pipeline-v2.3.0")

    r = client.post(
        f"/api/shortlist/{a}/flag",
        json={
            "create_rule": True,
            "rule": {
                "employer_pattern": "Acme*",
                "function_pattern": "sales_engineer",
            },
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["rule_id"] is not None

    # All three side effects present.
    assert t._query_one(
        "SELECT 1 FROM eval_labels WHERE opportunity_id = ?", (a,),
    ) is not None
    assert t._query_one(
        "SELECT flagged_at FROM eval_decisions "
        "WHERE opportunity_id = ? ORDER BY id DESC LIMIT 1",
        (a,),
    )["flagged_at"] is not None
    rule = t._query_one(
        "SELECT employer_pattern, function_pattern, active "
        "FROM flag_rules WHERE id = ?",
        (body["rule_id"],),
    )
    assert rule["employer_pattern"] == "Acme*"
    assert rule["function_pattern"] == "sales_engineer"
    assert rule["active"] == 1


def test_flag_updates_decision_at_cloud_version(harness):
    """The latest eval_decision could be at ANY of the family
    versions; flag must update it regardless."""
    client, t = harness
    a = _add(t, "A", "PM", "https://x/1")
    _seed_eval(t, a, "TOP_TIER", evaluator_version="gemma4-cloud-v1")
    client.post(f"/api/shortlist/{a}/flag", json={"create_rule": False})
    dec = t._query_one(
        "SELECT evaluator_version, tier, flagged_at FROM eval_decisions "
        "WHERE opportunity_id = ? ORDER BY id DESC LIMIT 1",
        (a,),
    )
    assert dec["evaluator_version"] == "gemma4-cloud-v1"
    assert dec["tier"] == "SKIP"
    assert dec["flagged_at"] is not None


def test_flag_updates_decision_at_fallback_version(harness):
    client, t = harness
    a = _add(t, "A", "PM", "https://x/1")
    _seed_eval(
        t, a, "STRONG", evaluator_version="pipeline-v2.3.0-fallback",
    )
    client.post(f"/api/shortlist/{a}/flag", json={"create_rule": False})
    dec = t._query_one(
        "SELECT tier, flagged_at FROM eval_decisions "
        "WHERE opportunity_id = ? ORDER BY id DESC LIMIT 1",
        (a,),
    )
    assert dec["tier"] == "SKIP"
    assert dec["flagged_at"] is not None


def test_flag_excludes_posting_from_shortlist(harness):
    client, t = harness
    a = _add(t, "A", "PM", "https://x/1")
    _seed_eval(t, a, "TOP_TIER", evaluator_version="gemma4-cloud-v1")
    # In shortlist before flagging.
    pre = client.get("/api/shortlist").json()
    assert a in [i["opportunity_id"] for i in pre]
    # Flag → tier becomes SKIP, falls out of TOP_TIER/STRONG filter.
    client.post(f"/api/shortlist/{a}/flag", json={"create_rule": False})
    post = client.get("/api/shortlist").json()
    assert a not in [i["opportunity_id"] for i in post]


def test_flag_all_null_rule_400(harness):
    client, t = harness
    a = _add(t, "A", "PM", "https://x/1")
    r = client.post(
        f"/api/shortlist/{a}/flag",
        json={"create_rule": True, "rule": {}},
    )
    assert r.status_code == 400
    assert "all-null rule" in r.json()["detail"]


def test_flag_unknown_posting_404(harness):
    client, _ = harness
    r = client.post(
        "/api/shortlist/99999/flag", json={"create_rule": False},
    )
    assert r.status_code == 404


class _RaisingProxyConn:
    """sqlite3.Connection proxy that raises on the Nth matching call.

    Used to inject a failure mid-transaction so we can verify
    flag_opportunity_atomic rolls back cleanly. Wraps the real
    connection so non-targeted calls pass through.
    """

    def __init__(self, real, raise_on_sql_fragment, after_calls):
        self._real = real
        self._fragment = raise_on_sql_fragment
        self._after = after_calls
        self._hits = 0

    def execute(self, sql, params=()):
        if self._fragment in sql:
            self._hits += 1
            if self._hits >= self._after:
                raise sqlite3.IntegrityError(
                    "synthetic rollback test",
                )
        return self._real.execute(sql, params)

    def commit(self):
        return self._real.commit()

    def rollback(self):
        return self._real.rollback()

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_flag_transaction_rolls_back_on_rule_failure(tmp_path):
    """Inject a failure on the flag_rules INSERT and assert that
    the eval_label and eval_decision changes are rolled back."""
    db = tmp_path / "t.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        a = _add(t, "A", "PM", "https://x/1")
        _seed_eval(t, a, "TOP_TIER", evaluator_version="gemma4-cloud-v1")

        real_conn = t._conn
        t._conn = _RaisingProxyConn(
            real_conn,
            raise_on_sql_fragment="INSERT INTO flag_rules",
            after_calls=1,
        )
        try:
            with pytest.raises(TrackerError):
                t.flag_opportunity_atomic(
                    opportunity_id=a,
                    flagged_at=datetime.now(timezone.utc).isoformat(),
                    rule={"employer_pattern": "X*"},
                )
        finally:
            t._conn = real_conn

        # Rollback fired: eval_label NOT inserted, decision tier
        # still TOP_TIER, flagged_at still NULL, no flag_rules row.
        assert t._query_one(
            "SELECT 1 FROM eval_labels WHERE opportunity_id = ?", (a,),
        ) is None
        dec = t._query_one(
            "SELECT tier, flagged_at FROM eval_decisions "
            "WHERE opportunity_id = ? ORDER BY id DESC LIMIT 1",
            (a,),
        )
        assert dec["tier"] == "TOP_TIER"
        assert dec["flagged_at"] is None
        assert t._query_one(
            "SELECT COUNT(*) AS n FROM flag_rules"
        )["n"] == 0
    finally:
        t.close()


# --- Auto-skip at persist time -----------------------------------

def _persist_basic(tracker, employer, title, url, location="Toronto"):
    rec = OpportunityRecord(
        source="manual_entry",
        source_url=url,
        employer=employer,
        title=title,
        location=location,
        posting_text="x",
        date_discovered=datetime(2026, 5, 10, tzinfo=timezone.utc),
    )
    opp_id, _ = persist_record(tracker, rec)
    return opp_id


def test_insert_matching_rule_auto_skips_at_persist(harness):
    client, t = harness
    # Create an active rule employer="Acme*".
    t.insert_flag_rule(employer_pattern="Acme*")

    # Insert a new opportunity matching the rule.
    pid = _persist_basic(t, "Acme Corp", "PM", "https://x/1")

    # eval_decision auto-inserted at rule-skip-v1.
    dec = t._query_one(
        "SELECT evaluator_version, tier, flagged_at "
        "FROM eval_decisions WHERE opportunity_id = ?",
        (pid,),
    )
    assert dec is not None
    assert dec["evaluator_version"] == "rule-skip-v1"
    assert dec["tier"] == "SKIP"
    # Auto-skip is NOT a user flag.
    assert dec["flagged_at"] is None


def test_insert_non_matching_opportunity_not_skipped(harness):
    client, t = harness
    t.insert_flag_rule(employer_pattern="Acme*")
    pid = _persist_basic(t, "Foo Co", "PM", "https://x/1")
    dec = t._query_one(
        "SELECT 1 FROM eval_decisions WHERE opportunity_id = ?",
        (pid,),
    )
    assert dec is None  # no auto-skip


def test_disable_rule_stops_auto_skip(harness):
    client, t = harness
    rid = t.insert_flag_rule(employer_pattern="Acme*")
    t.set_flag_rule_active(rid, False)
    pid = _persist_basic(t, "Acme Corp", "PM", "https://x/1")
    assert t._query_one(
        "SELECT 1 FROM eval_decisions WHERE opportunity_id = ?", (pid,),
    ) is None


def test_reenable_rule_resumes_auto_skip(harness):
    client, t = harness
    rid = t.insert_flag_rule(employer_pattern="Acme*")
    t.set_flag_rule_active(rid, False)
    t.set_flag_rule_active(rid, True)
    pid = _persist_basic(t, "Acme Corp", "PM", "https://x/1")
    dec = t._query_one(
        "SELECT evaluator_version FROM eval_decisions "
        "WHERE opportunity_id = ?",
        (pid,),
    )
    assert dec["evaluator_version"] == "rule-skip-v1"


# --- find_postings_needing_eval treats rule-skip-v1 as current ---

def test_rule_skip_v1_not_repicked_by_planner():
    """Regression on CURRENT_EVALUATOR_VERSIONS membership."""
    from scripts.run_full_eval_v2_3 import CURRENT_EVALUATOR_VERSIONS
    assert "rule-skip-v1" in CURRENT_EVALUATOR_VERSIONS


def test_find_postings_needing_eval_skips_rule_auto_skips(tmp_path):
    from scripts.run_daily import find_postings_needing_eval
    t = Tracker(profile_id="p", db_path=tmp_path / "t.db")
    try:
        t.insert_flag_rule(employer_pattern="Acme*")
        pid = _persist_basic(t, "Acme Corp", "PM", "https://x/1")
        # rule-skip-v1 already attached by persist_record.
        out = find_postings_needing_eval(t, limit=10)
        assert pid not in [p["id"] for p in out]
    finally:
        t.close()


# --- Few-shot selector prefers user_flagged ----------------------

def test_few_shot_selector_prefers_user_flagged_skips(tmp_path):
    """With 3 user_flagged SKIPs + 5 other SKIPs, k_per_tier=5
    returns all 3 user_flagged + 2 random non-flagged."""
    from skills.role_evaluator.few_shot import FewShotSelector

    t = Tracker(profile_id="p", db_path=tmp_path / "t.db")
    try:
        flagged_ids = []
        other_ids = []
        for i in range(3):
            pid = _add(t, f"FlaggedCo{i}", f"PM-f{i}",
                       f"https://x/f{i}")
            t.insert_eval_label(
                pid, verdict="skip", reason="user_flagged",
            )
            flagged_ids.append(pid)
        for i in range(5):
            pid = _add(t, f"OtherCo{i}", f"PM-o{i}",
                       f"https://x/o{i}")
            t.insert_eval_label(
                pid, verdict="skip", reason="role_mismatch",
            )
            other_ids.append(pid)

        sel = FewShotSelector(t)
        picked = sel.select(current_posting_id=9999, k_per_tier=5)
        skip_picked = [
            e for e in picked if e.human_tier == "SKIP"
        ]
        assert len(skip_picked) == 5
        n_flagged = sum(
            1 for e in skip_picked
            if e.human_reasoning == "user_flagged"
        )
        n_other = sum(
            1 for e in skip_picked
            if e.human_reasoning != "user_flagged"
        )
        # All 3 user_flagged should be present + 2 from the other pool.
        assert n_flagged == 3
        assert n_other == 2
    finally:
        t.close()


def test_few_shot_selector_ignores_rule_skip_v1_decisions(tmp_path):
    """Auto-skipped postings (only in eval_decisions at
    'rule-skip-v1', NOT in eval_labels) must NOT leak into the
    few-shot pool."""
    from skills.role_evaluator.few_shot import FewShotSelector

    t = Tracker(profile_id="p", db_path=tmp_path / "t.db")
    try:
        t.insert_flag_rule(employer_pattern="Acme*")
        # persist auto-skips via rule, writing eval_decisions only.
        pid = _persist_basic(t, "Acme Corp", "PM", "https://x/1")
        # No eval_label for this one.
        assert t._query_one(
            "SELECT 1 FROM eval_labels WHERE opportunity_id = ?",
            (pid,),
        ) is None

        sel = FewShotSelector(t)
        picked = sel.select(current_posting_id=999, k_per_tier=5)
        assert all(e.posting_id != pid for e in picked)
    finally:
        t.close()
