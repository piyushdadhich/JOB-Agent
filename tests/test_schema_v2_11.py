"""Tests for schema v2.11 migration, fresh-DB path, and backfill."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.persistence.tracker import Tracker  # noqa: E402

V2_SCHEMA_PATH = PROJECT_ROOT / "data" / "schemas" / "v2" / "schema.sql"
MIGRATION_PATHS = [
    PROJECT_ROOT / "data" / "schemas" / f"v2_{n}" / "migration.sql"
    for n in (2, 3, 4, 5, 6, 7, 8, 9, 10)
]

V211_OPPORTUNITY_COLUMNS = (
    "function",
    "industry_normalized",
    "city",
    "ai_subtype",
    "eval_priority",
)


def _column_names(conn, table):
    return [
        r[1] for r in conn.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()
    ]


def _bootstrap_v210(db_path: Path) -> None:
    """Build a v2.10-baseline DB by replaying migrations v2 -> v2.10."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(V2_SCHEMA_PATH.read_text(encoding="utf-8"))
        for path in MIGRATION_PATHS:
            conn.executescript(path.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


def _seed_opportunity(
    conn, *, employer, employer_norm, title, location,
    url, search_context=None,
):
    cur = conn.execute(
        "SELECT id FROM companies WHERE name_normalized = ?",
        (employer_norm,),
    ).fetchone()
    if cur is None:
        cur = conn.execute(
            "INSERT INTO companies "
            "(name, name_normalized, first_seen_at, last_seen_at) "
            "VALUES (?, ?, ?, ?)",
            (employer, employer_norm,
             "2026-05-07T00:00:00Z", "2026-05-07T00:00:00Z"),
        )
        cid = cur.lastrowid
    else:
        cid = cur[0]
    h = url.replace("/", "_").replace(":", "_")[:64]
    sc = json.dumps(search_context) if search_context is not None else None
    cur = conn.execute(
        "INSERT INTO opportunities "
        "(company_id, source, source_url, url_hash, title, location, "
        " search_context, date_discovered, last_seen_at) "
        "VALUES (?, 'manual_entry', ?, ?, ?, ?, ?, ?, ?)",
        (cid, url, h, title, location, sc,
         "2026-05-07T00:00:00Z", "2026-05-07T00:00:00Z"),
    )
    return cur.lastrowid


def test_migration_bumps_schema_version_to_211(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v210(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        # Tracker auto-applies later migrations too; v2.12 is the
        # current latest. The v2.11 columns asserted in the sibling
        # test still land regardless.
        assert t.schema_version() >= 211
    finally:
        t.close()


def test_migration_adds_opportunity_columns(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v210(db)
    t = Tracker(profile_id="p", db_path=db)
    try:
        cols = _column_names(t._conn, "opportunities")
        for expected in V211_OPPORTUNITY_COLUMNS:
            assert expected in cols, expected
    finally:
        t.close()


def test_fresh_db_uses_v2_11_schema_directly(tmp_path):
    db = tmp_path / "fresh.db"
    t = Tracker(profile_id="p", db_path=db)
    try:
        # A fresh DB now uses v2.12 (the current latest schema), but
        # the v2.11 opportunity columns still land via that schema.
        assert t.schema_version() >= 211
        cols = _column_names(t._conn, "opportunities")
        for expected in V211_OPPORTUNITY_COLUMNS:
            assert expected in cols, expected
    finally:
        t.close()


def test_constructor_idempotent_on_v2_11_db(tmp_path):
    """Re-opening a v2.11 DB does not re-apply or fail."""
    db = tmp_path / "t.db"
    Tracker(profile_id="p", db_path=db).close()
    t2 = Tracker(profile_id="p", db_path=db)
    try:
        assert t2.schema_version() >= 211
    finally:
        t2.close()


def test_backfill_populates_city(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v210(db)
    conn = sqlite3.connect(str(db))
    try:
        a = _seed_opportunity(
            conn, employer="A", employer_norm="a",
            title="PM", location="Toronto, ON",
            url="https://a/1",
        )
        b = _seed_opportunity(
            conn, employer="B", employer_norm="b",
            title="PM", location="Calgary, AB",
            url="https://b/1",
        )
        c = _seed_opportunity(
            conn, employer="C", employer_norm="c",
            title="PM", location="Vancouver",
            url="https://c/1",
        )
        conn.commit()
    finally:
        conn.close()

    t = Tracker(profile_id="p", db_path=db)
    try:
        rows = {
            r["id"]: r for r in t._query_all(
                "SELECT id, city FROM opportunities"
            )
        }
        assert rows[a]["city"] == "Toronto"
        assert rows[b]["city"] == "Calgary"
        assert rows[c]["city"] is None
    finally:
        t.close()


def test_backfill_populates_ai_subtype_and_priority(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v210(db)
    conn = sqlite3.connect(str(db))
    try:
        a = _seed_opportunity(
            conn, employer="A", employer_norm="a",
            title="AI Engineer", location="Toronto",
            url="https://a/1",
        )
        b = _seed_opportunity(
            conn, employer="B", employer_norm="b",
            title="Senior Project Manager", location="Toronto",
            url="https://b/1",
        )
        conn.commit()
    finally:
        conn.close()

    t = Tracker(profile_id="p", db_path=db)
    try:
        rows = {
            r["id"]: r for r in t._query_all(
                "SELECT id, ai_subtype, eval_priority FROM opportunities"
            )
        }
        assert rows[a]["ai_subtype"] == "ai_engineer"
        assert rows[a]["eval_priority"] == 1
        assert rows[b]["ai_subtype"] is None
        assert rows[b]["eval_priority"] == 2
    finally:
        t.close()


def test_backfill_pulls_function_and_industry_from_reasoning(tmp_path):
    db = tmp_path / "t.db"
    _bootstrap_v210(db)
    conn = sqlite3.connect(str(db))
    try:
        a = _seed_opportunity(
            conn, employer="A", employer_norm="a",
            title="PM", location="Toronto",
            url="https://a/1",
        )
        conn.execute(
            "INSERT INTO eval_decisions "
            "(opportunity_id, evaluator_version, tier, "
            " stage_trace, reasoning, evaluated_at) "
            "VALUES (?, 'test', 'STRONG', '{}', ?, ?)",
            (a, json.dumps({
                "role_type": "project_manager",
                "employer_industry": "banking",
            }), "2026-05-08T00:00:00Z"),
        )
        conn.commit()
    finally:
        conn.close()

    t = Tracker(profile_id="p", db_path=db)
    try:
        row = t._query_one(
            "SELECT function, industry_normalized "
            "FROM opportunities WHERE id = ?",
            (a,),
        )
        assert row["function"] == "project_manager"
        assert row["industry_normalized"] == "banking"
    finally:
        t.close()
