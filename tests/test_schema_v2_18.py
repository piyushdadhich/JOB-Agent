"""Tests for schema v2.18 migration + fresh-DB path.

v2.18 (Spec H1) adds (additive only): six new tables for the Phase 9
Targeted Outreach Agent:
  * persons
  * person_emails
  * person_scores
  * outreach_targets
  * company_email_formats
  * do_not_contact
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.persistence.tracker import Tracker  # noqa: E402

V2_SCHEMA_PATH = PROJECT_ROOT / "data" / "schemas" / "v2" / "schema.sql"
MIGRATION_PATHS_THROUGH_V217 = [
    PROJECT_ROOT / "data" / "schemas" / f"v2_{n}" / "migration.sql"
    for n in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17)
]

OUTREACH_TABLES = {
    "persons",
    "person_emails",
    "person_scores",
    "outreach_targets",
    "company_email_formats",
    "do_not_contact",
}


def _table_names(conn) -> set[str]:
    return {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _column_names(conn, table):
    return [
        r[1] for r in conn.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()
    ]


def _bootstrap_v217(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(V2_SCHEMA_PATH.read_text(encoding="utf-8"))
        for path in MIGRATION_PATHS_THROUGH_V217:
            if not path.exists():
                continue
            conn.executescript(path.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


def test_fresh_db_creates_all_outreach_tables(tmp_path):
    db = tmp_path / "fresh.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        present = _table_names(t._conn)
        missing = OUTREACH_TABLES - present
        assert not missing, f"missing tables: {missing}"
    finally:
        t.close()


def test_migration_v217_to_v218_creates_outreach_tables(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v217(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert t.schema_version() >= 218
        present = _table_names(t._conn)
        missing = OUTREACH_TABLES - present
        assert not missing, f"missing tables: {missing}"
    finally:
        t.close()


def test_migration_v217_to_v218_idempotent(tmp_path):
    """Opening the same DB twice must not fail and the v2.18 surface
    (six outreach tables) survives. Version may be >= 218 once later
    migrations stack on top (v2.19 adds a column)."""
    db = tmp_path / "t.db"
    Tracker(profile_id="p", db_path=db).close()
    t2 = Tracker(profile_id="p", db_path=db)
    try:
        assert t2.schema_version() >= 218
        present = _table_names(t2._conn)
        assert OUTREACH_TABLES <= present
    finally:
        t2.close()


def test_schema_version_is_at_least_218(tmp_path):
    # v2.18 introduced the outreach tables; later migrations may bump
    # the version (v2.19 adds personal_site_url). The contract here is
    # that the v2.18 surface is present, not that the version equals 218.
    db = tmp_path / "fresh.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert t.schema_version() >= 218
    finally:
        t.close()


def test_persons_columns(tmp_path):
    db = tmp_path / "fresh.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        cols = set(_column_names(t._conn, "persons"))
        expected = {
            "id", "source", "source_url", "full_name", "headline",
            "current_employer", "current_title", "linkedin_url",
            "github_username", "email_primary", "email_source",
            "email_confidence", "location", "personalization_hooks",
            "raw_payload", "date_discovered", "last_seen_at",
            "company_id", "job_poster_id",
        }
        missing = expected - cols
        assert not missing, f"missing columns: {missing}"
    finally:
        t.close()


def test_persons_linkedin_url_is_unique(tmp_path):
    db = tmp_path / "fresh.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        # First insert must succeed.
        t._conn.execute(
            "INSERT INTO persons (source, full_name, linkedin_url, "
            "date_discovered, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("github", "A", "https://linkedin.com/in/a",
             "2026-05-14T00:00:00+00:00",
             "2026-05-14T00:00:00+00:00"),
        )
        # Second with same linkedin_url must violate UNIQUE.
        raised = False
        try:
            t._conn.execute(
                "INSERT INTO persons (source, full_name, linkedin_url, "
                "date_discovered, last_seen_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("manual", "B", "https://linkedin.com/in/a",
                 "2026-05-14T00:00:00+00:00",
                 "2026-05-14T00:00:00+00:00"),
            )
        except sqlite3.IntegrityError:
            raised = True
        assert raised, "expected UNIQUE constraint on linkedin_url"
    finally:
        t.close()
