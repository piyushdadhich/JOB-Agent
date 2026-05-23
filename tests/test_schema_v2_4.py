"""Tests for schema v2.4 migration and fresh-DB path."""

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


def _bootstrap_v23(db_path: Path) -> None:
    """Build v2 -> v2.2 -> v2.3 baseline."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(V2_SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.executescript(V22_MIGRATION_PATH.read_text(encoding="utf-8"))
    conn.executescript(V23_MIGRATION_PATH.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def _bootstrap_v2(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(V2_SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def test_migration_creates_match_scores_table(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v23(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert _table_exists(t._conn, "match_scores")
        cols = _column_names(t._conn, "match_scores")
        for expected in (
            "opportunity_id", "scorer_version", "overlap_count",
            "posting_skill_count", "inventory_skill_count",
            "coverage_raw", "coverage_idf",
            "overlap_skill_ids", "missed_skill_ids", "bucket",
            "gemma_score", "gemma_summary",
            "gemma_hallucination_flags", "scored_at",
        ):
            assert expected in cols, expected
    finally:
        t.close()


def test_migration_creates_skill_labels_table(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v23(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert _table_exists(t._conn, "skill_labels")
        cols = _column_names(t._conn, "skill_labels")
        for expected in (
            "skill_id", "label", "taxonomy", "match_type", "source",
        ):
            assert expected in cols, expected
    finally:
        t.close()


def test_migration_bumps_schema_version_to_24(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v23(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert t.schema_version() >= 24
    finally:
        t.close()


def test_fresh_db_uses_v2_4_schema_directly(tmp_path):
    db = tmp_path / "fresh.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert t.schema_version() >= 24
        assert _table_exists(t._conn, "match_scores")
        assert _table_exists(t._conn, "skill_labels")
    finally:
        t.close()


def test_migration_chain_v2_to_v24_works(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v2(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert t.schema_version() >= 24
        assert _table_exists(t._conn, "match_scores")
        assert _table_exists(t._conn, "skill_labels")
        # v2.2 + v2.3 features still present.
        assert _table_exists(t._conn, "profile_skills")
        cols_opp = _column_names(t._conn, "opportunities")
        assert "extracted_skill_ids" in cols_opp
        assert "extracted_skill_ids_secondary" in cols_opp
    finally:
        t.close()
