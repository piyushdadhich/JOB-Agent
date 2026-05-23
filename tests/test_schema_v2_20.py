"""Tests for schema v2.20 migration + fresh-DB path.

v2.20 adds (additive only) to eval_decisions:
  * letter_grade     (TEXT, nullable) — A/B/C/D/F
  * interview_plan   (TEXT, nullable) — JSON list of talking points
  * red_flags        (TEXT, nullable) — JSON list of {flag,severity}
  * culture_signals  (TEXT, nullable) — JSON list of {signal,sentiment}
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.persistence.tracker import Tracker  # noqa: E402

V2_SCHEMA_PATH = PROJECT_ROOT / "data" / "schemas" / "v2" / "schema.sql"
MIGRATION_PATHS_THROUGH_V219 = [
    PROJECT_ROOT / "data" / "schemas" / f"v2_{n}" / "migration.sql"
    for n in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17,
              18, 19)
]


def _column_names(conn, table):
    return [
        r[1] for r in conn.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()
    ]


def _bootstrap_v219(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(V2_SCHEMA_PATH.read_text(encoding="utf-8"))
        for path in MIGRATION_PATHS_THROUGH_V219:
            if not path.exists():
                continue
            conn.executescript(path.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


def test_fresh_db_has_v220_eval_columns(tmp_path):
    db = tmp_path / "fresh.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        cols = _column_names(t._conn, "eval_decisions")
        for c in (
            "letter_grade", "interview_plan",
            "red_flags", "culture_signals",
        ):
            assert c in cols, (c, cols)
    finally:
        t.close()


def test_migration_v219_to_v220_adds_columns(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v219(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        # Migration chain auto-advances all the way to the latest
        # schema (currently v2.21). The point of this test is that
        # the v2.20 columns are present afterwards.
        assert t.schema_version() >= 220
        cols = _column_names(t._conn, "eval_decisions")
        for c in (
            "letter_grade", "interview_plan",
            "red_flags", "culture_signals",
        ):
            assert c in cols, (c, cols)
    finally:
        t.close()


def test_schema_version_is_at_least_220(tmp_path):
    """Renamed from `test_schema_version_is_220`: the bar is now
    "at least 220", since FIX-6 brought the head to v2.21 and any
    future migration will continue past that."""
    db = tmp_path / "fresh.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert t.schema_version() >= 220
    finally:
        t.close()
