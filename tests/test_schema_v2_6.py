"""Tests for schema v2.6 migration and fresh-DB path."""

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


def _table_exists(conn, name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _column_names(conn, table):
    return [
        r[1] for r in conn.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()
    ]


def _bootstrap_v25(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(V2_SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.executescript(V22_MIGRATION_PATH.read_text(encoding="utf-8"))
    conn.executescript(V23_MIGRATION_PATH.read_text(encoding="utf-8"))
    conn.executescript(V24_MIGRATION_PATH.read_text(encoding="utf-8"))
    conn.executescript(V25_MIGRATION_PATH.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def _bootstrap_v2(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(V2_SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def test_migration_creates_stage_decisions_table(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v25(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert _table_exists(t._conn, "stage_decisions")
        cols = _column_names(t._conn, "stage_decisions")
        for expected in (
            "opportunity_id", "stage_name", "stage_version",
            "decision", "reason", "metadata", "decided_at",
        ):
            assert expected in cols, expected
    finally:
        t.close()


def test_migration_bumps_schema_version_to_26(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v25(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert t.schema_version() >= 26
    finally:
        t.close()


def test_fresh_db_uses_v2_6_schema_directly(tmp_path):
    db = tmp_path / "fresh.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert t.schema_version() >= 26
        assert _table_exists(t._conn, "stage_decisions")
    finally:
        t.close()


def test_migration_chain_v2_to_v26_works(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v2(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert t.schema_version() >= 26
        assert _table_exists(t._conn, "stage_decisions")
        assert _table_exists(t._conn, "eval_labels")
        assert _table_exists(t._conn, "match_scores")
        assert _table_exists(t._conn, "skill_labels")
    finally:
        t.close()
