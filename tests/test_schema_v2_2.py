"""Tests for schema v2.2 migration and fresh-DB path."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.persistence.tracker import Tracker


V2_SCHEMA_PATH = PROJECT_ROOT / "data" / "schemas" / "v2" / "schema.sql"


def _column_names(conn, table):
    return [
        r[1] for r in conn.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()
    ]


def _table_exists(conn, name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _bootstrap_v2(db_path: Path) -> None:
    """Create a v2-baseline DB (schema_version=2) for migration testing."""
    sql = V2_SCHEMA_PATH.read_text(encoding="utf-8")
    conn = sqlite3.connect(str(db_path))
    conn.executescript(sql)
    conn.commit()
    conn.close()


def test_migration_adds_extracted_skill_ids_to_opportunities(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v2(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        cols = _column_names(t._conn, "opportunities")
        assert "extracted_skill_ids" in cols
    finally:
        t.close()


def test_migration_adds_inventory_skill_ids_to_companies(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v2(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        cols = _column_names(t._conn, "companies")
        assert "inventory_skill_ids" in cols
    finally:
        t.close()


def test_migration_creates_profile_skills_table(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v2(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert _table_exists(t._conn, "profile_skills")
        cols = _column_names(t._conn, "profile_skills")
        for expected in (
            "profile_id", "taxonomy", "skill_ids",
            "extracted_at", "source_doc", "raw_extraction",
        ):
            assert expected in cols
    finally:
        t.close()


def test_migration_bumps_schema_version_to_22(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v2(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        # >= 22 expresses the real invariant: v2.2 features are
        # present. Future migrations bump the version higher.
        assert t.schema_version() >= 22
    finally:
        t.close()


def test_fresh_db_uses_v2_2_schema_directly(tmp_path):
    db = tmp_path / "fresh.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert t.schema_version() >= 22
        cols_opp = _column_names(t._conn, "opportunities")
        cols_co = _column_names(t._conn, "companies")
        assert "extracted_skill_ids" in cols_opp
        assert "inventory_skill_ids" in cols_co
        assert _table_exists(t._conn, "profile_skills")
    finally:
        t.close()


def test_migration_is_idempotent(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v2(db)
    t1 = Tracker(profile_id="p", db_path=db)
    t1.close()
    # Reopening at v2.2 must not re-run the migration (which would
    # fail with "duplicate column name" on ALTER TABLE).
    t2 = Tracker(profile_id="p", db_path=db)
    try:
        assert t2.schema_version() >= 22
    finally:
        t2.close()
