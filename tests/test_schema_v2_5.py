"""Tests for schema v2.5 migration and fresh-DB path."""

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


def _bootstrap_v24(db_path: Path) -> None:
    """Build v2 -> v2.2 -> v2.3 -> v2.4 baseline."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(V2_SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.executescript(V22_MIGRATION_PATH.read_text(encoding="utf-8"))
    conn.executescript(V23_MIGRATION_PATH.read_text(encoding="utf-8"))
    conn.executescript(V24_MIGRATION_PATH.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def _bootstrap_v2(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(V2_SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def test_migration_creates_eval_labels_table(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v24(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert _table_exists(t._conn, "eval_labels")
        cols = _column_names(t._conn, "eval_labels")
        for expected in (
            "opportunity_id", "verdict", "reason",
            "notes", "labeled_at", "labeled_by",
        ):
            assert expected in cols, expected
    finally:
        t.close()


def test_migration_bumps_schema_version_to_25(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v24(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert t.schema_version() >= 25
    finally:
        t.close()


def test_fresh_db_uses_v2_5_schema_directly(tmp_path):
    db = tmp_path / "fresh.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert t.schema_version() >= 25
        assert _table_exists(t._conn, "eval_labels")
        # v2.4 features still present.
        assert _table_exists(t._conn, "match_scores")
        assert _table_exists(t._conn, "skill_labels")
    finally:
        t.close()


def test_migration_chain_v2_to_v25_works(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v2(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert t.schema_version() >= 25
        assert _table_exists(t._conn, "eval_labels")
        assert _table_exists(t._conn, "match_scores")
        assert _table_exists(t._conn, "skill_labels")
        assert _table_exists(t._conn, "profile_skills")
    finally:
        t.close()


def test_eval_labels_verdict_check_constraint(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v24(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        # Create an opportunity to FK to.
        cid = t.upsert_company(name="Acme")
        opp_id, _ = t.insert_opportunity(
            company_id=cid, source="x",
            source_url="https://x/1", title="A",
        )
        # Invalid verdict should raise via TrackerError
        with pytest.raises(Exception):
            t.insert_eval_label(opp_id, "bogus")
    finally:
        t.close()
