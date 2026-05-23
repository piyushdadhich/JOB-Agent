"""Tests for schema v2.8 migration and fresh-DB path.

v2.8 adds the outreach_drafts table (Phase 9 — Targeted Outreach
Agent). Status enum is enforced at the SQL CHECK level; transitions
are enforced in tracker code.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.persistence.tracker import Tracker  # noqa: E402

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
V27_MIGRATION_PATH = (
    PROJECT_ROOT / "data" / "schemas" / "v2_7" / "migration.sql"
)


def _column_names(conn, table):
    return [
        r[1] for r in conn.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()
    ]


def _bootstrap_v27(db_path: Path) -> None:
    """Build a v2.7-baseline DB by replaying migrations v2 -> v27."""
    conn = sqlite3.connect(str(db_path))
    try:
        for path in (
            V2_SCHEMA_PATH, V22_MIGRATION_PATH, V23_MIGRATION_PATH,
            V24_MIGRATION_PATH, V25_MIGRATION_PATH,
            V26_MIGRATION_PATH, V27_MIGRATION_PATH,
        ):
            conn.executescript(path.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


def test_migration_bumps_schema_version_to_28(tmp_path):
    """v2.8 added outreach_drafts. Chain may run further; we just
    verify the v2.8 contract is met (schema_version >= 28)."""
    db = tmp_path / "t.db"
    _bootstrap_v27(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert t.schema_version() >= 28
    finally:
        t.close()


def test_fresh_db_uses_v2_8_schema_directly(tmp_path):
    db = tmp_path / "fresh.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert t.schema_version() >= 28
        cols = _column_names(t._conn, "outreach_drafts")
        for expected in (
            "id", "company_id", "company_name", "subject", "body",
            "reason", "status", "generated_at", "reviewed_at", "sent_at",
        ):
            assert expected in cols, expected
    finally:
        t.close()


def test_outreach_drafts_status_check_constraint(tmp_path):
    """SQL CHECK rejects unknown status values."""
    db = tmp_path / "t.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        cid = t.upsert_company("ACME")
        with pytest.raises(sqlite3.IntegrityError):
            t._conn.execute(
                "INSERT INTO outreach_drafts "
                "(company_id, company_name, status, generated_at) "
                "VALUES (?, ?, 'mystery', ?)",
                (cid, "ACME", "2026-05-07T00:00:00Z"),
            )
    finally:
        t.close()
