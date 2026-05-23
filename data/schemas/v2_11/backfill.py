"""One-shot backfill of v2.11 classification columns.

Invoked by tracker._apply_migrations after v2_11/migration.sql adds
the new columns. Pure Python so we can JSON-parse
eval_decisions.reasoning to extract role_type and employer_industry,
which would be too clumsy in SQL.

For every opportunity:
  city          = classify_city(location)
  ai_subtype    = classify_ai_subtype(title, search_context)
  eval_priority = derive_eval_priority(ai_subtype)
  function              = latest eval_decisions.reasoning["role_type"]
                          if reasoning is JSON and key present, else NULL
  industry_normalized   = latest eval_decisions.reasoning[
                              "employer_industry"]
                          if reasoning is JSON and key present, else NULL

Idempotent on the column values.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Optional

# data/schemas/v2_11/backfill.py is loaded by tracker._apply_migrations
# via importlib.util (because data/schemas/ is not a Python package),
# so the engine import below resolves through the project root's
# sys.path entry. Add it defensively in case we were loaded out of
# tree.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from engine.persistence.classifiers import (  # noqa: E402
    classify_ai_subtype,
    classify_city,
    derive_eval_priority,
)

logger = logging.getLogger(__name__)


def _parse_search_context(raw: Optional[str]) -> Optional[dict]:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _latest_reasoning_dict(
    conn: sqlite3.Connection, opportunity_id: int,
) -> Optional[dict]:
    row = conn.execute(
        "SELECT reasoning FROM eval_decisions "
        "WHERE opportunity_id = ? "
        "ORDER BY evaluated_at DESC LIMIT 1",
        (opportunity_id,),
    ).fetchone()
    if row is None or not row[0]:
        return None
    try:
        parsed = json.loads(row[0])
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def run(conn: sqlite3.Connection) -> dict:
    """Populate classification columns on every opportunity row."""
    rows = conn.execute(
        "SELECT id, title, location, search_context FROM opportunities"
    ).fetchall()

    counts = {
        "total": 0, "with_city": 0, "with_ai_subtype": 0,
        "with_function": 0, "with_industry_normalized": 0,
        "priority_1": 0,
    }

    for r in rows:
        counts["total"] += 1
        opp_id, title, location, sc_raw = r[0], r[1], r[2], r[3]

        city = classify_city(location)
        ai_subtype = classify_ai_subtype(
            title, _parse_search_context(sc_raw),
        )
        priority = derive_eval_priority(ai_subtype)

        latest = _latest_reasoning_dict(conn, opp_id)
        function: Optional[str] = None
        industry: Optional[str] = None
        if latest is not None:
            rt = latest.get("role_type")
            ind = latest.get("employer_industry")
            if isinstance(rt, str) and rt.strip():
                function = rt.strip()
            if isinstance(ind, str) and ind.strip():
                industry = ind.strip()

        conn.execute(
            "UPDATE opportunities SET "
            "  city = ?, ai_subtype = ?, eval_priority = ?, "
            "  function = ?, industry_normalized = ? "
            "WHERE id = ?",
            (city, ai_subtype, priority, function, industry, opp_id),
        )

        if city:
            counts["with_city"] += 1
        if ai_subtype:
            counts["with_ai_subtype"] += 1
        if function:
            counts["with_function"] += 1
        if industry:
            counts["with_industry_normalized"] += 1
        if priority == 1:
            counts["priority_1"] += 1

    conn.commit()
    logger.info("v2.11 classification backfill: %s", counts)
    return counts
