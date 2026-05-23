"""Spec D1 TASK 2 — employer "go-deep" promotion strategy.

Companies with `min_count`+ STRONG / TOP_TIER postings in the
lookback window become candidates for the go-deep watchlist —
once user-confirmed (via `tracker.set_company_deep_target`), the
downstream ATS catalog fetcher pulls ALL their open roles
regardless of title filter. Deep-target state persists in the
companies.is_deep_target column (schema v2.16).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass
class DeepTarget:
    company_name: str
    company_id: int
    strong_count: int
    top_tier_count: int
    total_scored: int
    ats_platform: str | None
    ats_slug: str | None
    already_watched: bool


class EmployerDeepPromoter:
    """Identify companies that should be promoted to the
    go-deep watchlist based on recent scoring tier history.
    """

    _ALLOWED_MIN_TIERS = {
        "TOP_TIER": ("TOP_TIER",),
        "STRONG":   ("TOP_TIER", "STRONG"),
    }

    def __init__(
        self,
        tracker,
        min_tier: str = "STRONG",
        min_count: int = 2,
    ) -> None:
        if min_tier not in self._ALLOWED_MIN_TIERS:
            raise ValueError(
                f"min_tier must be one of {sorted(self._ALLOWED_MIN_TIERS)}; "
                f"got {min_tier!r}"
            )
        if min_count < 1:
            raise ValueError("min_count must be >= 1")
        self.tracker = tracker
        self.min_tier = min_tier
        self.min_count = min_count
        self._tiers = self._ALLOWED_MIN_TIERS[min_tier]

    def identify(self, days_back: int = 14) -> list[DeepTarget]:
        """Return companies with at least `min_count` qualifying
        postings (tier in `min_tier`'s set) in the past `days_back`
        days. Sorted by total qualifying count DESC, then top_tier
        count DESC.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days_back)
        ).isoformat()

        placeholders = ",".join("?" * len(self._tiers))
        rows = self.tracker._query_all(
            f"SELECT c.id AS cid, c.name AS employer, "
            f"       c.ats_platform, c.ats_slug, "
            f"       c.is_deep_target, latest.tier "
            f"FROM opportunities o "
            f"JOIN companies c ON c.id = o.company_id "
            f"JOIN ("
            f"  SELECT e1.* FROM eval_decisions e1 "
            f"  JOIN ("
            f"    SELECT opportunity_id, MAX(evaluated_at) AS max_at "
            f"    FROM eval_decisions GROUP BY opportunity_id"
            f"  ) e2 ON e1.opportunity_id = e2.opportunity_id "
            f"     AND e1.evaluated_at = e2.max_at"
            f") latest ON latest.opportunity_id = o.id "
            f"WHERE latest.tier IN ({placeholders}) "
            f"  AND latest.evaluated_at >= ?",
            tuple(self._tiers) + (cutoff,),
        )

        bucket_by_cid: dict[int, dict] = {}
        for r in rows:
            cid = r["cid"]
            b = bucket_by_cid.setdefault(cid, {
                "name": r["employer"],
                "ats_platform": r["ats_platform"],
                "ats_slug": r["ats_slug"],
                "is_deep_target": bool(r["is_deep_target"]),
                "strong": 0,
                "top_tier": 0,
            })
            if r["tier"] == "STRONG":
                b["strong"] += 1
            elif r["tier"] == "TOP_TIER":
                b["top_tier"] += 1

        out: list[DeepTarget] = []
        for cid, d in bucket_by_cid.items():
            total = d["strong"] + d["top_tier"]
            if total < self.min_count:
                continue
            out.append(DeepTarget(
                company_name=d["name"],
                company_id=cid,
                strong_count=d["strong"],
                top_tier_count=d["top_tier"],
                total_scored=total,
                ats_platform=d["ats_platform"],
                ats_slug=d["ats_slug"],
                already_watched=d["is_deep_target"],
            ))
        out.sort(key=lambda e: (-e.total_scored, -e.top_tier_count))
        return out
