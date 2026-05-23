"""Spec D1 TASK 1 — title cluster discovery strategy.

Groups STRONG / TOP_TIER eval_decisions by skill-profile similarity
(Jaccard on each posting's top-5 IDF-weighted skill IDs) and
identifies the modal job title per cluster. Clusters flagged with
`already_in_target=False` are role types scoring well that aren't
yet in the profile's target_role_types.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from engine.matching.scorer import compute_idf


_TOP_K_SKILLS = 5
_JACCARD_THRESHOLD = 0.6


@dataclass
class TitleCluster:
    modal_title: str
    posting_count: int
    skill_profile: set[str]
    example_employers: list[str] = field(default_factory=list)
    confidence: float = 0.0
    already_in_target: bool = False


def _top_k_skill_ids(
    skill_ids_raw: list[str],
    idf: dict[str, float],
    default_idf: float = 1.0,
    k: int = _TOP_K_SKILLS,
) -> set[str]:
    """Return the top-k skill IDs by IDF weight as a set. Deduped
    via set() first so duplicates can't double-rank.
    """
    deduped = set(skill_ids_raw)
    ranked = sorted(
        deduped,
        key=lambda sid: idf.get(sid, default_idf),
        reverse=True,
    )
    return set(ranked[:k])


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _normalize_target_role(s: str) -> str:
    """Profile target_role_types are snake_case slugs (e.g.
    'delivery_manager'). Posting titles are human strings (e.g.
    'Senior Delivery Manager'). Normalize both for a substring
    comparison.
    """
    return (s or "").lower().replace("_", " ").strip()


class TitleClusterAnalyzer:
    """Group STRONG/TOP_TIER postings by skill profile similarity
    and identify the modal job title per cluster.
    """

    _ALLOWED_MIN_TIERS = {
        "TOP_TIER": ("TOP_TIER",),
        "STRONG":   ("TOP_TIER", "STRONG"),
    }

    def __init__(self, tracker, min_tier: str = "STRONG") -> None:
        if min_tier not in self._ALLOWED_MIN_TIERS:
            raise ValueError(
                f"min_tier must be one of {sorted(self._ALLOWED_MIN_TIERS)}; "
                f"got {min_tier!r}"
            )
        self.tracker = tracker
        self.min_tier = min_tier
        self._tiers = self._ALLOWED_MIN_TIERS[min_tier]

    def analyze(
        self,
        days_back: int = 14,
        target_role_types: list[str] | None = None,
    ) -> list[TitleCluster]:
        """Return clusters of size >= 3 found in the past N days.

        target_role_types: profile.target_role_types slugs. Each
        cluster gets `already_in_target=True` iff any slug
        (normalized) appears as a substring of the modal title
        (case-insensitive).
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days_back)
        ).isoformat()

        placeholders = ",".join("?" * len(self._tiers))
        rows = self.tracker._query_all(
            f"SELECT o.id AS pid, o.title, c.name AS employer, "
            f"       o.extracted_skill_ids, latest.tier "
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
            f"  AND latest.evaluated_at >= ? "
            f"ORDER BY latest.evaluated_at DESC",
            tuple(self._tiers) + (cutoff,),
        )

        postings: list[dict] = []
        for r in rows:
            raw = r["extracted_skill_ids"]
            if not raw:
                continue
            try:
                ids = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not ids:
                continue
            postings.append({
                "pid": r["pid"],
                "title": r["title"] or "",
                "employer": r["employer"] or "",
                "skill_ids": ids,
            })

        if not postings:
            return []

        idf = compute_idf(self.tracker)

        for p in postings:
            p["profile"] = _top_k_skill_ids(p["skill_ids"], idf)

        # Greedy clustering: each posting joins the first existing
        # cluster whose centroid has Jaccard >= threshold against
        # the posting's profile; otherwise it seeds a new cluster.
        clusters: list[list[dict]] = []
        for p in postings:
            placed = False
            for cluster in clusters:
                centroid = cluster[0]["profile"]
                if _jaccard(p["profile"], centroid) >= _JACCARD_THRESHOLD:
                    cluster.append(p)
                    placed = True
                    break
            if not placed:
                clusters.append([p])

        targets = [_normalize_target_role(t) for t in (target_role_types or [])]

        out: list[TitleCluster] = []
        for cluster in clusters:
            if len(cluster) < 3:
                continue
            title_counts = Counter(m["title"] for m in cluster if m["title"])
            if not title_counts:
                continue
            modal_title, modal_n = title_counts.most_common(1)[0]
            # Cluster strength = avg pairwise Jaccard to centroid.
            centroid = cluster[0]["profile"]
            sims = [
                _jaccard(m["profile"], centroid) for m in cluster[1:]
            ]
            confidence = (
                (sum(sims) + 1.0) / (len(sims) + 1)
                if sims else 1.0
            )
            # Union of cluster skill profiles for reporting.
            shared: set[str] = set()
            for m in cluster:
                shared = shared | m["profile"] if shared else set(m["profile"])
            # Use intersection for the displayed shared profile.
            intersected = set(cluster[0]["profile"])
            for m in cluster[1:]:
                intersected &= m["profile"]

            example_employers: list[str] = []
            for m in cluster:
                emp = m["employer"]
                if emp and emp not in example_employers:
                    example_employers.append(emp)
                if len(example_employers) >= 5:
                    break

            modal_lower = modal_title.lower()
            already_in_target = any(
                t and t in modal_lower for t in targets
            )

            out.append(TitleCluster(
                modal_title=modal_title,
                posting_count=len(cluster),
                skill_profile=intersected or shared,
                example_employers=example_employers,
                confidence=confidence,
                already_in_target=already_in_target,
            ))

        out.sort(key=lambda c: (-c.posting_count, -c.confidence))
        return out
