"""Tests for schema v2.10 migration and fresh-DB path.

v2.10 adds 2 nullable columns to applications for auto-prompt
generation (Spec 3): resume_prompt and cover_letter_prompt. The
cloud pipeline pre-builds these for every TOP_TIER / STRONG posting
so the dashboard's Prompts queue is populated before the user opens
it.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

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
V29_MIGRATION_PATH = (
    PROJECT_ROOT / "data" / "schemas" / "v2_9" / "migration.sql"
)


V210_APPLICATION_COLUMNS = (
    "resume_prompt",
    "cover_letter_prompt",
)


def _column_names(conn, table):
    return [
        r[1] for r in conn.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()
    ]


def _bootstrap_v29(db_path: Path) -> None:
    """Build a v2.9-baseline DB by replaying migrations v2 -> v29."""
    conn = sqlite3.connect(str(db_path))
    try:
        for path in (
            V2_SCHEMA_PATH, V22_MIGRATION_PATH, V23_MIGRATION_PATH,
            V24_MIGRATION_PATH, V25_MIGRATION_PATH,
            V26_MIGRATION_PATH, V27_MIGRATION_PATH,
            V28_MIGRATION_PATH, V29_MIGRATION_PATH,
        ):
            conn.executescript(path.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


def test_migration_bumps_schema_version_to_210(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v29(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        # Tracker auto-applies later migrations too; v2.11 is the
        # current latest. The v2.10 columns asserted in the sibling
        # test still land regardless.
        assert t.schema_version() >= 210
    finally:
        t.close()


def test_migration_adds_application_columns(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v29(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        cols = _column_names(t._conn, "applications")
        for expected in V210_APPLICATION_COLUMNS:
            assert expected in cols, expected
    finally:
        t.close()


def test_fresh_db_uses_v2_10_schema_directly(tmp_path):
    db = tmp_path / "fresh.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        # A fresh DB now uses v2.11 (the current latest schema), but
        # the v2.10 application columns still land via that schema.
        assert t.schema_version() >= 210
        cols = _column_names(t._conn, "applications")
        for expected in V210_APPLICATION_COLUMNS:
            assert expected in cols, expected
    finally:
        t.close()


def test_prompts_writeable_via_update_fields(tmp_path):
    """auto_generate_prompts writes resume_prompt / cover_letter_prompt
    through tracker.update_application_fields; the whitelist must
    allow them or the call raises TrackerError."""
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
            resume_prompt="resume prompt body",
            cover_letter_prompt="cover letter prompt body",
        )
        row = t.get_application_by_id(app_id)
        assert row["resume_prompt"] == "resume prompt body"
        assert row["cover_letter_prompt"] == "cover letter prompt body"
    finally:
        t.close()


def test_existing_application_rows_preserved_through_migration(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v29(db)
    # Seed an application row at v2.9 using the v2.9 schema.
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
        for col in V210_APPLICATION_COLUMNS:
            assert rows[0][col] is None
    finally:
        t.close()
