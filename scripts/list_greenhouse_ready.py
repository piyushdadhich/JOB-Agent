"""Show Greenhouse postings ready for an apply.py dry-run.

Filters:
  - source_url contains 'greenhouse'
  - opportunity status = 'new'
  - latest eval tier is STRONG or TOP_TIER
  - no application has been started yet
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.persistence.tracker import Tracker


SQL = """\
SELECT o.id, o.title, c.name AS employer,
       o.source_url, latest.tier, latest.fit_score
FROM opportunities o
JOIN companies c ON c.id = o.company_id
JOIN (
  SELECT e1.* FROM eval_decisions e1
  JOIN (
    SELECT opportunity_id, MAX(evaluated_at) AS max_at
    FROM eval_decisions GROUP BY opportunity_id
  ) e2 ON e1.opportunity_id = e2.opportunity_id
     AND e1.evaluated_at = e2.max_at
) latest ON latest.opportunity_id = o.id
WHERE o.source_url LIKE '%greenhouse%'
  AND o.status = 'new'
  AND latest.tier IN ('TOP_TIER', 'STRONG')
  AND NOT EXISTS (
    SELECT 1 FROM applications WHERE opportunity_id = o.id
  )
ORDER BY latest.fit_score DESC, o.date_discovered DESC
LIMIT 20
"""


def main():
    t = Tracker("default")
    try:
        rows = t._query_all(SQL)
        # Broader stats.
        all_gh = t._query_all(
            "SELECT o.id, o.status, latest.tier "
            "FROM opportunities o "
            "LEFT JOIN ("
            "  SELECT e1.* FROM eval_decisions e1 "
            "  JOIN ("
            "    SELECT opportunity_id, MAX(evaluated_at) AS max_at "
            "    FROM eval_decisions GROUP BY opportunity_id"
            "  ) e2 ON e1.opportunity_id = e2.opportunity_id "
            "     AND e1.evaluated_at = e2.max_at"
            ") latest ON latest.opportunity_id = o.id "
            "WHERE o.source_url LIKE '%greenhouse%'"
        )
        # All STRONG/TOP_TIER (any source).
        all_strong = t._query_all(
            "SELECT o.id, o.title, c.name AS employer, "
            "       o.source_url, o.status, latest.tier, latest.fit_score "
            "FROM opportunities o "
            "JOIN companies c ON c.id = o.company_id "
            "JOIN ("
            "  SELECT e1.* FROM eval_decisions e1 "
            "  JOIN ("
            "    SELECT opportunity_id, MAX(evaluated_at) AS max_at "
            "    FROM eval_decisions GROUP BY opportunity_id"
            "  ) e2 ON e1.opportunity_id = e2.opportunity_id "
            "     AND e1.evaluated_at = e2.max_at"
            ") latest ON latest.opportunity_id = o.id "
            "WHERE latest.tier IN ('TOP_TIER', 'STRONG') "
            "  AND o.status = 'new' "
            "  AND NOT EXISTS ("
            "    SELECT 1 FROM applications WHERE opportunity_id = o.id"
            "  ) "
            "ORDER BY latest.fit_score DESC LIMIT 15"
        )
    finally:
        t.close()

    print(f"\n=== Total Greenhouse opps in DB: {len(all_gh)} ===")
    by_status: dict[str, int] = {}
    by_tier: dict[str, int] = {}
    for r in all_gh:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        tier = r["tier"] or "(no eval)"
        by_tier[tier] = by_tier.get(tier, 0) + 1
    print(f"  By status:  {by_status}")
    print(f"  By tier:    {by_tier}")

    print(f"\n=== Greenhouse opps ready for dry-run: {len(rows)} ===")
    for r in rows:
        print(
            f"  #{r['id']:>4}  fit={r['fit_score']}  "
            f"{r['tier']:>9}  {(r['employer'] or '')[:25]:25}  "
            f"{(r['title'] or '')[:55]}"
        )

    print(f"\n=== ALL ATSes ready for dry-run "
          f"(STRONG/TOP_TIER, status=new, not applied): "
          f"{len(all_strong)} ===")
    for r in all_strong:
        ats_hint = ""
        url = r["source_url"] or ""
        for tag in ("greenhouse", "lever", "ashby", "myworkdayjobs",
                    "indeed", "linkedin"):
            if tag in url.lower():
                ats_hint = tag
                break
        print(
            f"  #{r['id']:>4}  fit={r['fit_score']}  "
            f"{r['tier']:>9}  {ats_hint:>10}  "
            f"{(r['employer'] or '')[:22]:22}  "
            f"{(r['title'] or '')[:42]}"
        )


if __name__ == "__main__":
    main()
