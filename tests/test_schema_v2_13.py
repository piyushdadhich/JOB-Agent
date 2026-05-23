"""Tests for schema v2.13 migration + fresh-DB path.

v2.13 drops three columns that were 0% populated in production:
  opportunities.notes, opportunities.tags, companies.size_label.
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

V2_13_DROPPED_OPPORTUNITY_COLUMNS = ("notes", "tags")
V2_13_DROPPED_COMPANY_COLUMNS = ("size_label",)


def _column_names(conn, table):
    return [
        r[1] for r in conn.execute(
            f"PRAGMA table_info({table})"
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


def test_schema_v2_13_columns_dropped(tmp_path):
    """Opening a Tracker on a v2.10 baseline auto-migrates through v2.13
    and removes the three dead columns."""
    db = tmp_path / "t.db"
    _bootstrap_v210(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        opp_cols = _column_names(t._conn, "opportunities")
        for col in V2_13_DROPPED_OPPORTUNITY_COLUMNS:
            assert col not in opp_cols, (
                f"opportunities.{col} unexpectedly still present: {opp_cols}"
            )
        company_cols = _column_names(t._conn, "companies")
        for col in V2_13_DROPPED_COMPANY_COLUMNS:
            assert col not in company_cols, (
                f"companies.{col} unexpectedly still present: {company_cols}"
            )
        # Sanity: notes columns on OTHER tables must remain.
        for other_table in (
            "applications", "recruiters", "eval_labels", "communications",
            "companies",  # companies.notes is a different column from size_label
        ):
            other_cols = _column_names(t._conn, other_table)
            assert "notes" in other_cols, (
                f"{other_table}.notes was dropped — should be retained"
            )
    finally:
        t.close()


def test_fresh_db_uses_v2_13_schema(tmp_path):
    """A brand-new DB skips migrations and applies v2_13/schema.sql
    directly; schema_version is 213 on first open."""
    db = tmp_path / "fresh.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        # >= 213 so the test remains valid after subsequent v2.14+ migrations.
        assert t.schema_version() >= 213
        opp_cols = _column_names(t._conn, "opportunities")
        assert "notes" not in opp_cols
        assert "tags" not in opp_cols
        company_cols = _column_names(t._conn, "companies")
        assert "size_label" not in company_cols
    finally:
        t.close()


def test_migration_v2_12_to_v2_13_bumps_schema_version(tmp_path):
    """A DB bootstrapped to v2.10 then opened via Tracker migrates all
    the way to v2.13 — schema_version == 213."""
    db = tmp_path / "t.db"
    _bootstrap_v210(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert t.schema_version() >= 213
    finally:
        t.close()


def test_migration_v2_12_to_v2_13_idempotent(tmp_path):
    """Re-opening a v2.13 DB does not re-apply the migration or fail."""
    db = tmp_path / "t.db"
    Tracker(profile_id="p", db_path=db).close()
    t2 = Tracker(profile_id="p", db_path=db)
    try:
        assert t2.schema_version() >= 213
        # And the dropped columns are still gone (didn't get re-added
        # by an accidental re-apply of an earlier schema).
        opp_cols = _column_names(t2._conn, "opportunities")
        assert "notes" not in opp_cols
        assert "tags" not in opp_cols
        company_cols = _column_names(t2._conn, "companies")
        assert "size_label" not in company_cols
    finally:
        t2.close()


def test_migration_chain_v2_to_v2_13(tmp_path):
    """Full chain from v2.0 baseline through every migration up to and
    including v2.13 lands at schema_version == 213 with the dropped
    columns gone."""
    db = tmp_path / "t.db"
    # Bootstrap only the v2.0 base schema; Tracker handles every
    # subsequent migration (v2.2 through v2.13) on first open.
    conn = sqlite3.connect(str(db))
    try:
        conn.executescript(V2_SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert t.schema_version() >= 213
        opp_cols = _column_names(t._conn, "opportunities")
        assert "notes" not in opp_cols
        assert "tags" not in opp_cols
        company_cols = _column_names(t._conn, "companies")
        assert "size_label" not in company_cols
        # And the v2.11 classification columns landed too (chain proof).
        for col in ("function", "industry_normalized", "city",
                    "ai_subtype", "eval_priority"):
            assert col in opp_cols, f"v2.11 column {col} missing"
    finally:
        t.close()
