"""Tests for schema v2.19 migration + fresh-DB path.

v2.19 adds (additive only):
  * persons.personal_site_url (TEXT, nullable) — captures the
    `blog` field from GitHub profiles or a personal-website URL
    parsed from a conference speaker bio.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.persistence.tracker import Tracker  # noqa: E402

V2_SCHEMA_PATH = PROJECT_ROOT / "data" / "schemas" / "v2" / "schema.sql"
MIGRATION_PATHS_THROUGH_V218 = [
    PROJECT_ROOT / "data" / "schemas" / f"v2_{n}" / "migration.sql"
    for n in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18)
]


def _column_names(conn, table):
    return [
        r[1] for r in conn.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()
    ]


def _bootstrap_v218(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(V2_SCHEMA_PATH.read_text(encoding="utf-8"))
        for path in MIGRATION_PATHS_THROUGH_V218:
            if not path.exists():
                continue
            conn.executescript(path.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


def test_fresh_db_has_persons_personal_site_url(tmp_path):
    db = tmp_path / "fresh.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        cols = _column_names(t._conn, "persons")
        assert "personal_site_url" in cols, cols
    finally:
        t.close()


def test_migration_v218_to_v219_adds_personal_site_url(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v218(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert t.schema_version() >= 219
        cols = _column_names(t._conn, "persons")
        assert "personal_site_url" in cols, cols
    finally:
        t.close()


def test_schema_version_at_least_219(tmp_path):
    db = tmp_path / "fresh.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert t.schema_version() >= 219
    finally:
        t.close()
