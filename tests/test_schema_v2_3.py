"""Tests for schema v2.3 migration and fresh-DB path."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.persistence.tracker import Tracker


V2_SCHEMA_PATH = PROJECT_ROOT / "data" / "schemas" / "v2" / "schema.sql"
V22_MIGRATION_PATH = (
    PROJECT_ROOT / "data" / "schemas" / "v2_2" / "migration.sql"
)


def _column_names(conn, table):
    return [
        r[1] for r in conn.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()
    ]


def _bootstrap_v22(db_path: Path) -> None:
    """Create a v2.2-baseline DB (schema_version=22) for v2.3
    migration testing. Builds v2 then layers v2.2 migration on top
    to mirror the historic upgrade path."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(V2_SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.executescript(V22_MIGRATION_PATH.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def _bootstrap_v2(db_path: Path) -> None:
    """Create a raw v2-baseline DB (schema_version=2) for full
    chain migration testing."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(V2_SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def test_migration_adds_extracted_skill_ids_secondary_to_opportunities(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v22(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        cols = _column_names(t._conn, "opportunities")
        assert "extracted_skill_ids_secondary" in cols
    finally:
        t.close()


def test_migration_adds_secondary_taxonomy_to_opportunities(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v22(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        cols = _column_names(t._conn, "opportunities")
        assert "secondary_taxonomy" in cols
    finally:
        t.close()


def test_migration_bumps_schema_version_to_23(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v22(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert t.schema_version() >= 23
    finally:
        t.close()


def test_fresh_db_uses_v2_3_schema_directly(tmp_path):
    db = tmp_path / "fresh.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert t.schema_version() >= 23
        cols = _column_names(t._conn, "opportunities")
        assert "extracted_skill_ids" in cols
        assert "extracted_skill_ids_secondary" in cols
        assert "secondary_taxonomy" in cols
    finally:
        t.close()


def test_migration_chain_v2_to_v23_works(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v2(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert t.schema_version() >= 23
        cols_opp = _column_names(t._conn, "opportunities")
        # v2.2 columns present (came in via v2 -> v2.2 step).
        assert "extracted_skill_ids" in cols_opp
        # v2.3 columns present (came in via v2.2 -> v2.3 step).
        assert "extracted_skill_ids_secondary" in cols_opp
        assert "secondary_taxonomy" in cols_opp
    finally:
        t.close()
