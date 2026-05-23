"""Tests for schema v2.17 migration + fresh-DB path.

v2.17 adds (additive only):
  * applications.follow_ups (TEXT, nullable) — JSON list of follow-up
    email events appended each time the user sends a follow-up from
    the Pipeline Tracker. Cold-start state is NULL.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.persistence.tracker import Tracker  # noqa: E402

V2_SCHEMA_PATH = PROJECT_ROOT / "data" / "schemas" / "v2" / "schema.sql"
MIGRATION_PATHS_THROUGH_V216 = [
    PROJECT_ROOT / "data" / "schemas" / f"v2_{n}" / "migration.sql"
    for n in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16)
]


def _column_names(conn, table):
    return [
        r[1] for r in conn.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()
    ]


def _bootstrap_v216(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(V2_SCHEMA_PATH.read_text(encoding="utf-8"))
        for path in MIGRATION_PATHS_THROUGH_V216:
            if not path.exists():
                # v2_11 has a backfill.py rather than a pure migration.sql
                # — the v2_11 migration.sql does exist; this guard keeps
                # the bootstrap forgiving if a version ever skips a file.
                continue
            conn.executescript(path.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


def test_fresh_db_has_applications_follow_ups(tmp_path):
    db = tmp_path / "fresh.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        cols = _column_names(t._conn, "applications")
        assert "follow_ups" in cols, cols
    finally:
        t.close()


def test_migration_v216_to_v217_adds_follow_ups(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v216(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert t.schema_version() >= 217
        cols = _column_names(t._conn, "applications")
        assert "follow_ups" in cols, cols
    finally:
        t.close()


def test_migration_v216_to_v217_idempotent(tmp_path):
    db = tmp_path / "t.db"
    Tracker(profile_id="p", db_path=db).close()
    t2 = Tracker(profile_id="p", db_path=db)
    try:
        assert t2.schema_version() >= 217
        cols = _column_names(t2._conn, "applications")
        assert "follow_ups" in cols
    finally:
        t2.close()


def test_schema_version_is_at_least_217(tmp_path):
    # v2.17 introduced applications.follow_ups; later migrations may
    # bump the version (v2.18, v2.19). The contract here is that the
    # v2.17 surface survives, not that the version equals 217.
    db = tmp_path / "fresh.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert t.schema_version() >= 217
    finally:
        t.close()


def test_follow_ups_default_is_null(tmp_path):
    """Cold-start: a freshly inserted application has follow_ups=NULL,
    not '[]'. The pipeline_tracker write path is responsible for the
    NULL-vs-[] handling so we don't burn storage on the empty case."""
    db = tmp_path / "t.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        cid = t.upsert_company(name="Acme")
        oid, _ = t.insert_opportunity(
            company_id=cid, source="workday",
            source_url="https://x/acme/pm",
            title="PM", location="Toronto", posting_text="...",
        )
        app_id = t.create_application(
            opportunity_id=oid, resume_variant="pending",
        )
        row = t._conn.execute(
            "SELECT follow_ups FROM applications WHERE id = ?",
            (app_id,),
        ).fetchone()
        assert row[0] is None
    finally:
        t.close()
