"""Tests for schema v2.7 migration and fresh-DB path.

v2.7 adds 6 nullable columns to companies for the ATS Detector
(Phase 5a Step 6): canonical_domain, ats_platform, ats_slug,
ats_detected_at, ats_detection_method, ats_detection_confidence.
"""

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
V23_MIGRATION_PATH = (
    PROJECT_ROOT / "data" / "schemas" / "v2_3" / "migration.sql"
)
V24_MIGRATION_PATH = (
    PROJECT_ROOT / "data" / "schemas" / "v2_4" / "migration.sql"
)
V25_MIGRATION_PATH = (
    PROJECT_ROOT / "data" / "schemas" / "v2_5" / "migration.sql"
)
V26_MIGRATION_PATH = (
    PROJECT_ROOT / "data" / "schemas" / "v2_6" / "migration.sql"
)


ATS_COLUMNS = (
    "canonical_domain",
    "ats_platform",
    "ats_slug",
    "ats_detected_at",
    "ats_detection_method",
    "ats_detection_confidence",
)


def _column_names(conn, table):
    return [
        r[1] for r in conn.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()
    ]


def _bootstrap_v26(db_path: Path) -> None:
    """Build a v2.6-baseline DB by replaying migrations v2 -> v26."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(V2_SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.executescript(V22_MIGRATION_PATH.read_text(encoding="utf-8"))
    conn.executescript(V23_MIGRATION_PATH.read_text(encoding="utf-8"))
    conn.executescript(V24_MIGRATION_PATH.read_text(encoding="utf-8"))
    conn.executescript(V25_MIGRATION_PATH.read_text(encoding="utf-8"))
    conn.executescript(V26_MIGRATION_PATH.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def test_migration_adds_ats_columns_to_companies(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v26(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        cols = _column_names(t._conn, "companies")
        for expected in ATS_COLUMNS:
            assert expected in cols, expected
    finally:
        t.close()


def test_migration_bumps_schema_version_to_27(tmp_path):
    """v2.7 was the migration that added ATS columns. After this
    test was written, v2.8 (outreach_drafts) chained on top, so the
    final version is now 28+ -- but the contract this test checks
    is 'v2.7 migration ran successfully', so >= 27 is what we want.
    """
    db = tmp_path / "t.db"
    _bootstrap_v26(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert t.schema_version() >= 27
    finally:
        t.close()


def test_fresh_db_uses_v2_7_schema_directly(tmp_path):
    """Fresh DB should at minimum include the v2.7 ATS columns
    (later migrations may have chained on top)."""
    db = tmp_path / "fresh.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert t.schema_version() >= 27
        cols = _column_names(t._conn, "companies")
        for expected in ATS_COLUMNS:
            assert expected in cols, expected
    finally:
        t.close()


def test_existing_company_rows_preserved_through_migration(tmp_path):
    """The migration is purely additive — existing rows survive
    untouched and the new columns default to NULL."""
    db = tmp_path / "t.db"
    _bootstrap_v26(db)

    # Insert a row at the v2.6 schema, then re-open at v2.7.
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO companies (name, name_normalized, "
        "first_seen_at, last_seen_at) VALUES (?, ?, ?, ?)",
        ("Acme", "acme", "2026-01-01T00:00:00+00:00",
         "2026-01-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    t = Tracker(profile_id="p", db_path=db)
    try:
        row = t.get_company_by_name("Acme")
        assert row is not None
        for col in ATS_COLUMNS:
            assert row[col] is None, f"{col} should default to NULL"
    finally:
        t.close()


def test_ats_platform_index_exists(tmp_path):
    """The migration creates idx_companies_ats_platform for fast
    lookups when populating slug lists."""
    db = tmp_path / "fresh.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        rows = t._conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND name='idx_companies_ats_platform'"
        ).fetchall()
        assert len(rows) == 1
    finally:
        t.close()
