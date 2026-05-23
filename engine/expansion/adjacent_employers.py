"""Spec D1 TASK 3 — adjacent-employer suggestion strategy.

For each TOP_TIER employer, surface other companies in the SAME
industry that ALSO have postings in the configured target cities
but have NOT yet been scored STRONG / TOP_TIER. These are
"sister" companies the system already knows about but hasn't
prioritized.

By design this strategy does NOT discover companies the agent
has never seen — that needs an external company-search source
(LinkedIn directory, industry registry, etc.) which is out of
scope for v1. Phase 7 v2 will layer that on.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass
class AdjacentSuggestion:
    source_company: str
    source_industry: str
    suggested_company: str
    suggested_industry: str
    reason: str
    confidence: float


class AdjacentEmployerFinder:
    """Suggest companies in the same industry + target cities as
    known TOP_TIER employers.

    target_cities: list of normalized city names matching the
    `opportunities.city` column (set by v2.11 classify_city).
    Pass [] / None to disable the city filter.
    """

    def __init__(
        self,
        tracker,
        target_cities: list[str] | None = None,
    ) -> None:
        self.tracker = tracker
        self.target_cities = [c.lower() for c in (target_cities or [])]

    def suggest(self, days_back: int = 14) -> list[AdjacentSuggestion]:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days_back)
        ).isoformat()

        # 1. TOP_TIER employers with industries (in the lookback).
        top_rows = self.tracker._query_all(
            "SELECT DISTINCT c.id AS cid, c.name AS employer, "
            "       c.industry AS industry "
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
            "WHERE latest.tier = 'TOP_TIER' "
            "  AND latest.evaluated_at >= ? "
            "  AND c.industry IS NOT NULL AND c.industry != ''",
            (cutoff,),
        )

        # If no TOP_TIER employers (or none with industry) — nothing to suggest.
        if not top_rows:
            return []

        # 2. Companies that have ALREADY been scored STRONG / TOP_TIER
        # (any time, not just within the window) — exclude from suggestions.
        already_strong_rows = self.tracker._query_all(
            "SELECT DISTINCT c.id AS cid "
            "FROM companies c "
            "JOIN opportunities o ON o.company_id = c.id "
            "JOIN eval_decisions ed ON ed.opportunity_id = o.id "
            "WHERE ed.tier IN ('TOP_TIER', 'STRONG')",
        )
        already_strong: set[int] = {r["cid"] for r in already_strong_rows}

        # 3. For each TOP_TIER industry, find candidate companies. A
        # candidate has at least one opportunity in the target cities
        # (or any city if no filter), same industry, and is not
        # already strong/top_tier.
        out: list[AdjacentSuggestion] = []
        seen: set[tuple[int, int]] = set()  # (source_cid, candidate_cid)

        for row in top_rows:
            src_cid = row["cid"]
            src_industry = row["industry"]
            src_employer = row["employer"]

            if self.target_cities:
                city_placeholders = ",".join("?" * len(self.target_cities))
                cand_rows = self.tracker._query_all(
                    f"SELECT DISTINCT c.id AS cid, c.name AS name, "
                    f"       c.industry AS industry "
                    f"FROM companies c "
                    f"JOIN opportunities o ON o.company_id = c.id "
                    f"WHERE c.industry = ? "
                    f"  AND c.id != ? "
                    f"  AND LOWER(o.city) IN ({city_placeholders})",
                    (src_industry, src_cid) + tuple(self.target_cities),
                )
            else:
                cand_rows = self.tracker._query_all(
                    "SELECT DISTINCT c.id AS cid, c.name AS name, "
                    "       c.industry AS industry "
                    "FROM companies c "
                    "JOIN opportunities o ON o.company_id = c.id "
                    "WHERE c.industry = ? AND c.id != ?",
                    (src_industry, src_cid),
                )

            for cand in cand_rows:
                cand_cid = cand["cid"]
                if cand_cid in already_strong:
                    continue
                key = (src_cid, cand_cid)
                if key in seen:
                    continue
                seen.add(key)
                out.append(AdjacentSuggestion(
                    source_company=src_employer,
                    source_industry=src_industry,
                    suggested_company=cand["name"],
                    suggested_industry=cand["industry"],
                    reason="same industry + geography",
                    confidence=0.6,
                ))
        out.sort(key=lambda s: (s.suggested_industry, s.suggested_company))
        return out
