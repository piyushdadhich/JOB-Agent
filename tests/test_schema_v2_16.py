"""Tests for schema v2.16 migration + fresh-DB path.

v2.16 adds (additive only):
  * companies.is_deep_target (INTEGER NOT NULL DEFAULT 0) — go-deep
    watchlist flag used by the Spec D1 expansion agent.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.persistence.tracker import Tracker  # noqa: E402

V2_SCHEMA_PATH = PROJECT_ROOT / "data" / "schemas" / "v2" / "schema.sql"
MIGRATION_PATHS_THROUGH_V210 = [
    PROJECT_ROOT / "data" / "schemas" / f"v2_{n}" / "migration.sql"
    for n in (2, 3, 4, 5, 6, 7, 8, 9, 10)
]


def _column_names(conn, table):
    return [
        r[1] for r in conn.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()
    ]


def _column_info(conn, table):
    return list(conn.execute(f"PRAGMA table_info({table})").fetchall())


def _bootstrap_v210(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(V2_SCHEMA_PATH.read_text(encoding="utf-8"))
        for path in MIGRATION_PATHS_THROUGH_V210:
            conn.executescript(path.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


def test_fresh_db_has_companies_is_deep_target(tmp_path):
    db = tmp_path / "fresh.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        cols = _column_names(t._conn, "companies")
        assert "is_deep_target" in cols, cols
        info = _column_info(t._conn, "companies")
        col = [c for c in info if c[1] == "is_deep_target"][0]
        # cid, name, type, notnull, dflt_value, pk
        assert col[2].upper() == "INTEGER", col
        assert col[3] == 1, f"is_deep_target should be NOT NULL: {col}"
        assert str(col[4]) == "0", f"default should be 0: {col}"
    finally:
        t.close()


def test_migration_v215_to_v216_adds_is_deep_target(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v210(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert t.schema_version() >= 216
        cols = _column_names(t._conn, "companies")
        assert "is_deep_target" in cols, cols
    finally:
        t.close()


def test_migration_v215_to_v216_idempotent(tmp_path):
    db = tmp_path / "t.db"
    Tracker(profile_id="p", db_path=db).close()
    t2 = Tracker(profile_id="p", db_path=db)
    try:
        assert t2.schema_version() >= 216
        cols = _column_names(t2._conn, "companies")
        assert "is_deep_target" in cols
    finally:
        t2.close()


def test_schema_version_at_least_216(tmp_path):
    """Fresh DB reports schema_version >= 216 (chained beyond v2.16)."""
    db = tmp_path / "fresh.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        assert t.schema_version() >= 216
    finally:
        t.close()


def test_set_company_deep_target(tmp_path):
    """tracker.set_company_deep_target flips the flag."""
    db = tmp_path / "t.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        cid = t.upsert_company(name="Acme")
        row = t._conn.execute(
            "SELECT is_deep_target FROM companies WHERE id = ?", (cid,)
        ).fetchone()
        assert row[0] == 0
        t.set_company_deep_target(cid, True)
        row = t._conn.execute(
            "SELECT is_deep_target FROM companies WHERE id = ?", (cid,)
        ).fetchone()
        assert row[0] == 1
        t.set_company_deep_target(cid, False)
        row = t._conn.execute(
            "SELECT is_deep_target FROM companies WHERE id = ?", (cid,)
        ).fetchone()
        assert row[0] == 0
    finally:
        t.close()
