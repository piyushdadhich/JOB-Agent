"""Spec JA-3 TASK 1 -- populate opportunities.function and
opportunities.industry_normalized using the rule-based classifiers.

Rule-based first because 24,270 rows x ~5 sec/Gemma call is 33 hours.
Aim is dashboard filter dropdowns having usable data, not 100% precision.

Idempotent: only updates rows where the column is currently NULL or ''.
Run: .\\venv\\Scripts\\python.exe scripts/backfill_function_industry.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from engine.persistence.classifiers import (
    classify_function,
    classify_industry,
)
from engine.persistence.tracker import Tracker


def backfill(profile: str = "default") -> dict[str, int]:
    tracker = Tracker(profile)

    rows = tracker._query_all(
        "SELECT o.id, o.title, o.posting_text, o.function, "
        "o.industry_normalized, c.name AS company_name, "
        "c.industry AS company_industry "
        "FROM opportunities o "
        "LEFT JOIN companies c ON c.id = o.company_id",
        (),
    )

    fn_updates = 0
    ind_updates = 0
    fn_skipped = 0
    ind_skipped = 0

    for row in rows:
        opp_id = row["id"]

        fn_existing = (row["function"] or "").strip()
        if not fn_existing:
            fn = classify_function(row["title"], row["posting_text"])
            if fn:
                tracker._conn.execute(
                    "UPDATE opportunities SET function = ? WHERE id = ?",
                    (fn, opp_id),
                )
                fn_updates += 1
            else:
                fn_skipped += 1

        ind_existing = (row["industry_normalized"] or "").strip()
        if not ind_existing:
            ind = classify_industry(
                row["company_name"],
                row["company_industry"],
            )
            if ind:
                tracker._conn.execute(
                    "UPDATE opportunities SET industry_normalized = ? "
                    "WHERE id = ?",
                    (ind, opp_id),
                )
                ind_updates += 1
            else:
                ind_skipped += 1

    tracker._conn.commit()
    tracker.close()

    return {
        "function_updates": fn_updates,
        "function_skipped": fn_skipped,
        "industry_updates": ind_updates,
        "industry_skipped": ind_skipped,
        "total": len(rows),
    }


if __name__ == "__main__":
    result = backfill(profile="default")
    print("\n=== Backfill summary ===")
    print(f"Total rows scanned: {result['total']}")
    print(
        f"function: {result['function_updates']} updated, "
        f"{result['function_skipped']} unmatched"
    )
    print(
        f"industry_normalized: {result['industry_updates']} updated, "
        f"{result['industry_skipped']} unmatched"
    )
