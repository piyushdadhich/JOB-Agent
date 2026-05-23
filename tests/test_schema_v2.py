"""Tests for data/schemas/v2/schema.sql — structural assertions only."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

SCHEMA_PATH = PROJECT_ROOT / "data" / "schemas" / "v2" / "schema.sql"


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "schema_v2_test.db"
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    c.commit()
    yield c
    c.close()


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {r["name"] for r in rows}


def _index_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index'"
    ).fetchall()
    return {r["name"] for r in rows}


def test_schema_creates_all_required_tables(conn):
    tables = _table_names(conn)
    expected = {
        "companies", "opportunities", "eval_decisions",
        "recruiters", "applications", "status_history",
        "communications", "events", "schema_version",
    }
    missing = expected - tables
    assert not missing, f"missing tables: {missing}"


def test_schema_version_marker_is_2(conn):
    row = conn.execute(
        "SELECT version FROM schema_version"
    ).fetchone()
    assert row["version"] == 2


def test_foreign_keys_pragma_enabled_by_pragma(conn):
    conn.execute("PRAGMA foreign_keys = ON")
    val = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert val == 1


def test_companies_name_normalized_unique_constraint(conn):
    now = "2026-04-29T00:00:00+00:00"
    conn.execute(
        "INSERT INTO companies "
        "(name, name_normalized, first_seen_at, last_seen_at) "
        "VALUES (?, ?, ?, ?)",
        ("Acme", "acme", now, now),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO companies "
            "(name, name_normalized, first_seen_at, last_seen_at) "
            "VALUES (?, ?, ?, ?)",
            ("Acme Inc", "acme", now, now),
        )
        conn.commit()


def test_opportunities_url_hash_unique_constraint(conn):
    now = "2026-04-29T00:00:00+00:00"
    conn.execute(
        "INSERT INTO companies "
        "(name, name_normalized, first_seen_at, last_seen_at) "
        "VALUES ('Acme', 'acme', ?, ?)", (now, now),
    )
    company_id = conn.execute(
        "SELECT id FROM companies WHERE name_normalized = 'acme'"
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO opportunities "
        "(company_id, source, source_url, url_hash, title, "
        " date_discovered, last_seen_at) "
        "VALUES (?, 'jobspy', 'https://x/1', 'hash1', 'PM', ?, ?)",
        (company_id, now, now),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO opportunities "
            "(company_id, source, source_url, url_hash, title, "
            " date_discovered, last_seen_at) "
            "VALUES (?, 'jobspy', 'https://x/2', 'hash1', 'PM2', ?, ?)",
            (company_id, now, now),
        )
        conn.commit()


def test_eval_decisions_tier_check_constraint_rejects_invalid(conn):
    now = "2026-04-29T00:00:00+00:00"
    conn.execute(
        "INSERT INTO companies "
        "(name, name_normalized, first_seen_at, last_seen_at) "
        "VALUES ('X', 'x', ?, ?)", (now, now),
    )
    cid = conn.execute(
        "SELECT id FROM companies WHERE name_normalized = 'x'"
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO opportunities "
        "(company_id, source, source_url, url_hash, title, "
        " date_discovered, last_seen_at) "
        "VALUES (?, 'jobspy', 'https://x/1', 'h1', 't', ?, ?)",
        (cid, now, now),
    )
    oid = conn.execute(
        "SELECT id FROM opportunities WHERE url_hash = 'h1'"
    ).fetchone()["id"]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO eval_decisions "
            "(opportunity_id, evaluator_version, tier, stage_trace, "
            " evaluated_at) VALUES (?, 'v1', 'BOGUS', '{}', ?)",
            (oid, now),
        )
        conn.commit()


def test_eval_decisions_tier_check_accepts_valid(conn):
    now = "2026-04-29T00:00:00+00:00"
    conn.execute(
        "INSERT INTO companies "
        "(name, name_normalized, first_seen_at, last_seen_at) "
        "VALUES ('X', 'x', ?, ?)", (now, now),
    )
    cid = conn.execute(
        "SELECT id FROM companies WHERE name_normalized = 'x'"
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO opportunities "
        "(company_id, source, source_url, url_hash, title, "
        " date_discovered, last_seen_at) "
        "VALUES (?, 'jobspy', 'https://x/1', 'h1', 't', ?, ?)",
        (cid, now, now),
    )
    oid = conn.execute(
        "SELECT id FROM opportunities WHERE url_hash = 'h1'"
    ).fetchone()["id"]
    for tier in ("TOP_TIER", "STRONG", "EXPLORATORY", "SKIP", "EXCLUDED"):
        conn.execute(
            "INSERT INTO eval_decisions "
            "(opportunity_id, evaluator_version, tier, stage_trace, "
            " evaluated_at) VALUES (?, 'v1', ?, '{}', ?)",
            (oid, tier, now),
        )
    conn.commit()
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM eval_decisions"
    ).fetchone()["n"]
    assert n == 5


def test_applications_resume_variant_no_check_constraint(conn):
    """v2 removes the resume_variant CHECK so profile-driven variants work."""
    now = "2026-04-29T00:00:00+00:00"
    conn.execute(
        "INSERT INTO companies "
        "(name, name_normalized, first_seen_at, last_seen_at) "
        "VALUES ('X', 'x', ?, ?)", (now, now),
    )
    cid = conn.execute(
        "SELECT id FROM companies WHERE name_normalized = 'x'"
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO opportunities "
        "(company_id, source, source_url, url_hash, title, "
        " date_discovered, last_seen_at) "
        "VALUES (?, 'jobspy', 'https://x/1', 'h1', 't', ?, ?)",
        (cid, now, now),
    )
    oid = conn.execute(
        "SELECT id FROM opportunities WHERE url_hash = 'h1'"
    ).fetchone()["id"]
    # Insert with a non-v1 variant — should succeed under v2.
    conn.execute(
        "INSERT INTO applications "
        "(opportunity_id, resume_variant, status_updated_at) "
        "VALUES (?, 'startup_variant', ?)",
        (oid, now),
    )
    conn.commit()
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM applications"
    ).fetchone()["n"]
    assert n == 1


def test_required_indexes_exist(conn):
    indexes = _index_names(conn)
    required = {
        "idx_companies_name_normalized",
        "idx_opportunities_url_hash",
        "idx_opportunities_company_id",
        "idx_opportunities_discovered_at",
        "idx_opportunities_status",
        "idx_eval_decisions_opportunity_id",
        "idx_eval_decisions_tier",
        "idx_eval_decisions_evaluated_at",
        "idx_applications_opportunity_id",
        "idx_applications_status",
        "idx_recruiters_agency_name",
        "idx_status_history_application_id",
        "idx_communications_application_id",
        "idx_events_occurred_at",
        "idx_events_type",
    }
    missing = required - indexes
    assert not missing, f"missing indexes: {missing}"
