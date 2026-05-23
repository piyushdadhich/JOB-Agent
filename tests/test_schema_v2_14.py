"""Tests for schema v2.14 migration + fresh-DB path.

v2.14 adds (additive only — no existing columns dropped or modified):
  * opportunities.hiring_team_json (TEXT, nullable)
  * job_posters table + indexes
  * opportunity_posters table (composite PK for idempotency)
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.persistence.tracker import Tracker  # noqa: E402

V2_SCHEMA_PATH = PROJECT_ROOT / "data" / "schemas" / "v2" / "schema.sql"
# Migrations 2..10 are applied via raw SQL to bootstrap a v2.10 DB.
# Tracker's _apply_migrations then handles v2.11+ (the v2.11 step has
# a Python-side backfill, so it must run through Tracker, not direct SQL).
MIGRATION_PATHS_THROUGH_V210 = [
    PROJECT_ROOT / "data" / "schemas" / f"v2_{n}" / "migration.sql"
    for n in (2, 3, 4, 5, 6, 7, 8, 9, 10)
]


def _column_names(conn, table):
    return [
        r[1] for r in conn.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()
    ]


def _table_names(conn):
    return [
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]


def _bootstrap_v210(db_path: Path) -> None:
    """Build a v2.10-baseline DB by replaying v2 schema + migrations 2..10."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(V2_SCHEMA_PATH.read_text(encoding="utf-8"))
        for path in MIGRATION_PATHS_THROUGH_V210:
            conn.executescript(path.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


def test_schema_v2_14_adds_hiring_team_json_column(tmp_path):
    """Opening a Tracker on a v2.10 baseline auto-migrates through v2.14
    and adds the hiring_team_json column to opportunities."""
    db = tmp_path / "t.db"
    _bootstrap_v210(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        opp_cols = _column_names(t._conn, "opportunities")
        assert "hiring_team_json" in opp_cols, (
            f"hiring_team_json not added to opportunities: {opp_cols}"
        )
    finally:
        t.close()


def test_schema_v2_14_creates_job_posters_table(tmp_path):
    """v2.14 migration creates the job_posters table with the expected
    columns and indexes."""
    db = tmp_path / "t.db"
    _bootstrap_v210(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        tables = _table_names(t._conn)
        assert "job_posters" in tables, (
            f"job_posters table missing after migration: {tables}"
        )
        cols = _column_names(t._conn, "job_posters")
        for c in (
            "id", "linkedin_profile_url", "full_name", "title_at_posting",
            "employer_at_posting", "company_id", "first_seen_at",
            "last_seen_at", "total_postings_observed",
        ):
            assert c in cols, f"job_posters.{c} missing: {cols}"
        # Both indexes present
        idx_rows = t._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='job_posters'"
        ).fetchall()
        idx_names = {r[0] for r in idx_rows}
        assert "idx_job_posters_url" in idx_names, idx_names
        assert "idx_job_posters_name_employer" in idx_names, idx_names
    finally:
        t.close()


def test_schema_v2_14_creates_opportunity_posters_table(tmp_path):
    """v2.14 migration creates the opportunity_posters join table with
    a composite PK."""
    db = tmp_path / "t.db"
    _bootstrap_v210(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        tables = _table_names(t._conn)
        assert "opportunity_posters" in tables, (
            f"opportunity_posters table missing: {tables}"
        )
        cols = _column_names(t._conn, "opportunity_posters")
        for c in (
            "opportunity_id", "job_poster_id", "role_on_posting",
            "observed_at",
        ):
            assert c in cols, f"opportunity_posters.{c} missing: {cols}"
        # Verify composite PK by checking pk flags
        pk_cols = [
            r[1] for r in t._conn.execute(
                "PRAGMA table_info(opportunity_posters)"
            ).fetchall() if r[5] > 0
        ]
        assert set(pk_cols) == {
            "opportunity_id", "job_poster_id", "role_on_posting",
        }, f"unexpected PK columns: {pk_cols}"
    finally:
        t.close()


def test_fresh_db_uses_v2_14_schema(tmp_path):
    """A brand-new DB skips migrations and applies v2_14/schema.sql
    directly; schema_version is >= 214 on first open and all new
    structures are present."""
    db = tmp_path / "fresh.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        # >= 214 so this test remains valid after future migrations.
        assert t.schema_version() >= 214
        opp_cols = _column_names(t._conn, "opportunities")
        assert "hiring_team_json" in opp_cols
        tables = _table_names(t._conn)
        assert "job_posters" in tables
        assert "opportunity_posters" in tables
    finally:
        t.close()


def test_migration_v2_13_to_v2_14_bumps_schema_version(tmp_path):
    """A DB bootstrapped to v2.10 then opened via Tracker migrates all
    the way to v2.14 — schema_version >= 214."""
    db = tmp_path / "t.db"
    _bootstrap_v210(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert t.schema_version() >= 214
    finally:
        t.close()


def test_migration_v2_13_to_v2_14_idempotent(tmp_path):
    """Re-opening a v2.14 DB does not re-apply the migration or fail."""
    db = tmp_path / "t.db"
    Tracker(profile_id="p", db_path=db).close()
    t2 = Tracker(profile_id="p", db_path=db)
    try:
        assert t2.schema_version() >= 214
        # And the new structures are still present (no accidental
        # re-application that might have failed mid-flight).
        opp_cols = _column_names(t2._conn, "opportunities")
        assert "hiring_team_json" in opp_cols
        tables = _table_names(t2._conn)
        assert "job_posters" in tables
        assert "opportunity_posters" in tables
    finally:
        t2.close()
