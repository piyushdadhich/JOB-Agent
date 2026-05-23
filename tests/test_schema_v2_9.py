"""Tests for schema v2.9 migration and fresh-DB path.

v2.9 adds 6 nullable columns to applications for the Playwright
Application Agent (Phase 11): resume_text, cover_letter_text,
ats_platform, screenshot_path, submitted_url, screening_answers.
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
V28_MIGRATION_PATH = (
    PROJECT_ROOT / "data" / "schemas" / "v2_8" / "migration.sql"
)


V29_APPLICATION_COLUMNS = (
    "resume_text",
    "cover_letter_text",
    "ats_platform",
    "screenshot_path",
    "submitted_url",
    "screening_answers",
    "selected_at",
    "prompt_generated_at",
    "docs_ready_at",
)


def _column_names(conn, table):
    return [
        r[1] for r in conn.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()
    ]


def _bootstrap_v28(db_path: Path) -> None:
    """Build a v2.8-baseline DB by replaying migrations v2 -> v28."""
    conn = sqlite3.connect(str(db_path))
    try:
        for path in (
            V2_SCHEMA_PATH, V22_MIGRATION_PATH, V23_MIGRATION_PATH,
            V24_MIGRATION_PATH, V25_MIGRATION_PATH,
            V26_MIGRATION_PATH, V27_MIGRATION_PATH,
            V28_MIGRATION_PATH,
        ):
            conn.executescript(path.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


def test_migration_bumps_schema_version_to_29(tmp_path):
    """The migration chain runs to the latest schema (>= 29). The
    v2.9 columns are added by the v2.8 -> v2.9 migration step."""
    db = tmp_path / "t.db"
    _bootstrap_v28(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert t.schema_version() >= 29
    finally:
        t.close()


def test_migration_adds_application_columns(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v28(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        cols = _column_names(t._conn, "applications")
        for expected in V29_APPLICATION_COLUMNS:
            assert expected in cols, expected
    finally:
        t.close()


def test_fresh_db_uses_v2_9_schema_directly(tmp_path):
    """Fresh DBs land on the latest schema (>= 29) and include all
    v2.9 columns inline."""
    db = tmp_path / "fresh.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert t.schema_version() >= 29
        cols = _column_names(t._conn, "applications")
        for expected in V29_APPLICATION_COLUMNS:
            assert expected in cols, expected
    finally:
        t.close()


def test_lifecycle_timestamps_writeable_via_update_fields(tmp_path):
    """Dashboard writes selected_at / prompt_generated_at / docs_ready_at
    through tracker.update_application_fields; the whitelist must allow
    them or the call raises TrackerError."""
    db = tmp_path / "t.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        cid = t.upsert_company("Acme")
        oid, _ = t.insert_opportunity(
            company_id=cid, source="manual_entry",
            source_url="https://x", title="PM",
            location="Toronto", posting_text="x",
        )
        app_id = t.create_application(
            opportunity_id=oid, resume_variant="r.docx",
        )
        t.update_application_fields(
            app_id,
            selected_at="2026-05-07T10:00:00Z",
            prompt_generated_at="2026-05-07T10:05:00Z",
            docs_ready_at="2026-05-07T10:20:00Z",
        )
        row = t.get_application_by_id(app_id)
        assert row["selected_at"] == "2026-05-07T10:00:00Z"
        assert row["prompt_generated_at"] == "2026-05-07T10:05:00Z"
        assert row["docs_ready_at"] == "2026-05-07T10:20:00Z"
    finally:
        t.close()


def test_existing_application_rows_preserved_through_migration(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v28(db)
    # Seed an application row at v2.8 using the v2.8 schema.
    conn = sqlite3.connect(str(db))
    try:
        cur = conn.execute(
            "INSERT INTO companies "
            "(name, name_normalized, first_seen_at, last_seen_at) "
            "VALUES (?, ?, ?, ?)",
            ("Acme", "acme", "2026-05-07T00:00:00Z",
             "2026-05-07T00:00:00Z"),
        )
        cid = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO opportunities "
            "(company_id, source, source_url, url_hash, title, "
            " date_discovered, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (cid, "manual_entry", "http://x", "deadbeef",
             "PM", "2026-05-07T00:00:00Z", "2026-05-07T00:00:00Z"),
        )
        oid = cur.lastrowid
        conn.execute(
            "INSERT INTO applications "
            "(opportunity_id, resume_variant, status, "
            " status_updated_at) "
            "VALUES (?, ?, 'drafted', ?)",
            (oid, "resume.docx", "2026-05-07T00:00:00Z"),
        )
        conn.commit()
    finally:
        conn.close()

    t = Tracker(profile_id="p", db_path=db)
    try:
        rows = t._query_all("SELECT * FROM applications")
        assert len(rows) == 1
        # New columns are NULL for the pre-migration row.
        for col in V29_APPLICATION_COLUMNS:
            assert rows[0][col] is None
    finally:
        t.close()
