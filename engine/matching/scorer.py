"""Deterministic skill-matching scorer.

Two signals:
  coverage_raw -- |posting ∩ inventory| / |posting|
  coverage_idf -- IDF-weighted coverage:
                  sum(idf for matched) / sum(idf for posting)

IDF is computed empirically from the posting corpus: how many
postings contain each skill ID? Rare skills weigh more.

Both inventory and posting skill sets are deduped before
matching. The 0.30 confidence floor was already applied at
backfill time; the scorer operates on the persisted IDs as-is.

This module has NO ML dependencies. Runs in venv\\.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass

from engine.persistence.tracker import Tracker

SCORER_VERSION = "5d-step5a-v1"

# Bucket thresholds. Tuned at the spec level: zero-overlap is
# auto-skip, 1-3 is low-signal noise (small text, generic
# skills), 4+ is "enough overlap to ask Gemma".
BUCKET_LOW_MAX = 3


@dataclass
class MatchResult:
    opportunity_id: int
    overlap_count: int
    posting_skill_count: int
    inventory_skill_count: int
    coverage_raw: float
    coverage_idf: float
    overlap_skill_ids: list[str]
    missed_skill_ids: list[str]
    bucket: str  # 'zero', 'low', 'high'


def compute_idf(
    tracker: Tracker,
    min_df: int = 2,
) -> dict[str, float]:
    """Compute IDF for every skill ID across the posting corpus.

    idf(s) = log(N / df(s)) where:
      N      = total postings with non-NULL extracted_skill_ids
      df(s)  = number of postings containing skill s (deduped
               within posting)

    Skills appearing in fewer than min_df postings get capped at
    idf(min_df) -- i.e., treated as if they appeared in exactly
    min_df postings. This prevents single-occurrence outliers
    from dominating the IDF-weighted score.

    Returns {} on an empty corpus.
    """
    all_opps = tracker.list_opportunities(limit=10_000)
    opps_with_skills = [
        o for o in all_opps
        if o.get("extracted_skill_ids") is not None
    ]
    n = len(opps_with_skills)
    if n == 0:
        return {}

    df: Counter[str] = Counter()
    for opp in opps_with_skills:
        ids = set(json.loads(opp["extracted_skill_ids"]))
        for sid in ids:
            df[sid] += 1

    idf_cap = math.log(n / min_df) if min_df > 0 else 0.0

    idf: dict[str, float] = {}
    for sid, count in df.items():
        if count < min_df:
            idf[sid] = idf_cap
        else:
            idf[sid] = math.log(n / count)
    return idf


def load_inventory_skill_ids(
    tracker: Tracker,
    profile_id: str = "default",
    taxonomy: str = "lightcast",
) -> set[str]:
    """Load the inventory skill IDs for a profile from
    profile_skills. Returns the deduped set. Empty set if
    no row found.
    """
    row = tracker.get_profile_skills(profile_id, taxonomy)
    if not row or not row.get("skill_ids"):
        return set()
    return set(row["skill_ids"])


def score_opportunity(
    posting_skill_ids_raw: list[str],
    inventory_ids: set[str],
    idf: dict[str, float],
    default_idf: float = 1.0,
) -> MatchResult:
    """Score one opportunity against the inventory.

    posting_skill_ids_raw: JSON-deserialized list from
      extracted_skill_ids. May contain duplicates -- deduped
      via set() before matching.
    inventory_ids: deduped set from load_inventory_skill_ids.
    idf: precomputed IDF dict from compute_idf.
    default_idf: IDF value for skills not in the IDF dict.
      Default 1.0 (neutral). Should never trigger in practice
      since IDF was computed over the same corpus, but a sane
      default if a posting somehow has a skill not in IDF.
    """
    posting_ids = set(posting_skill_ids_raw)
    overlap = posting_ids & inventory_ids
    missed = posting_ids - inventory_ids

    posting_count = len(posting_ids)
    if posting_count == 0:
        return MatchResult(
            opportunity_id=0,
            overlap_count=0,
            posting_skill_count=0,
            inventory_skill_count=len(inventory_ids),
            coverage_raw=0.0,
            coverage_idf=0.0,
            overlap_skill_ids=[],
            missed_skill_ids=[],
            bucket="zero",
        )

    coverage_raw = len(overlap) / posting_count

    # IDF-weighted coverage:
    #   sum(idf for matched) / sum(idf for all posting skills)
    idf_matched = sum(
        idf.get(s, default_idf) for s in overlap
    )
    idf_total = sum(
        idf.get(s, default_idf) for s in posting_ids
    )
    coverage_idf = (
        idf_matched / idf_total if idf_total > 0 else 0.0
    )

    if len(overlap) == 0:
        bucket = "zero"
    elif len(overlap) <= BUCKET_LOW_MAX:
        bucket = "low"
    else:
        bucket = "high"

    return MatchResult(
        opportunity_id=0,  # set by caller
        overlap_count=len(overlap),
        posting_skill_count=posting_count,
        inventory_skill_count=len(inventory_ids),
        coverage_raw=coverage_raw,
        coverage_idf=coverage_idf,
        overlap_skill_ids=sorted(overlap),
        missed_skill_ids=sorted(missed),
        bucket=bucket,
    )


# -----------------------------------------------------------------------
# Spec B2 TASK 4 -- ThreeSignalScorer (coverage + rarity + bridge)
# -----------------------------------------------------------------------
#
# Per v4 architecture Section 6.4. Operates on skill ID sets only;
# no LLM involvement. Coexists with the legacy two-signal score_
# opportunity() above -- both are valid in different paths until
# Phase 6 picks one as the canonical evaluator scorer.
#
# Signal A -- Coverage: |posting ∩ inventory| / |posting|.
# Signal B -- Rarity: wired to compute_idf() (real signal, not stub).
#             posting_count==0 returns 0.0.
# Signal C -- Bridge: stubbed at 0.5 (constant). TODO Phase 6 --
#             needs the full Lightcast taxonomy hierarchy dump,
#             which is not in ojd_daps_skills output. Stubbed so
#             the scorer is usable today; the constant doesn't
#             affect relative ranking among postings.
# Tier thresholds (from v4 architecture):
#   TOP_TIER:    overall >= 7.5
#   STRONG:      overall >= 5.5
#   EXPLORATORY: overall >= 3.5
#   SKIP:        overall <  3.5

THREE_SIGNAL_SCORER_VERSION = "5d-step5-three-signal-v3"

# Tier thresholds — Spec B3 TASK 1 (4.0/2.5/1.0).
# v1 (7.5/5.5/3.5):  99.2% SKIP across min_score 0.5/0.3/0.2
# v2 (5.0/3.5/2.5):  73.1% SKIP after dropping type filter on both
#                    inventory + posting backfill
# v3 (3.5/2.0/1.0):  redefined SKIP as "essentially zero match";
#                    SKIP rate unchanged from v2 (cutoff is at 1.0,
#                    determined by the bridge stub baseline of 0.75
#                    for zero-match postings).
# v4 / B3 TASK 1 (4.0/2.5/1.0): tighter TOP_TIER + STRONG bands to
#                    better reflect the empirical 0-5.5 distribution.
#                    SKIP cutoff stays at 1.0; the real SKIP-rate fix
#                    comes from B3 TASK 2 (title pre-filter) + TASK 3
#                    (real bridge proximity from Lightcast hierarchy).
_TIER_TOP = 4.0
_TIER_STRONG = 2.5
_TIER_EXPLORATORY = 1.0

# Default coefficients per v4 Section 6.4 — tunable in Phase 6.
_COEF_COVERAGE = 0.5
_COEF_RARITY = 0.35
_COEF_BRIDGE = 0.15

_BRIDGE_STUB_VALUE = 0.5


@dataclass
class ScoringResult:
    coverage: float        # signal A, 0..1
    rarity: float          # signal B, 0..1
    bridge: float          # signal C, 0..1
    overall: float         # weighted sum * 10, 0..10
    tier: str              # TOP_TIER | STRONG | EXPLORATORY | SKIP
    matched: set[str]      # inventory ∩ posting
    missed: set[str]       # posting - inventory
    posting_count: int
    inventory_count: int


def _assign_tier(overall: float) -> str:
    if overall >= _TIER_TOP:
        return "TOP_TIER"
    if overall >= _TIER_STRONG:
        return "STRONG"
    if overall >= _TIER_EXPLORATORY:
        return "EXPLORATORY"
    return "SKIP"


class ThreeSignalScorer:
    """Deterministic three-signal scorer.

    `inventory_skill_ids` is the canonical set (e.g., from
    profile_skills for default/lightcast). `idf` is optional —
    when None, rarity collapses to coverage (equal-weight baseline,
    matching the spec's stub). Pass a real IDF dict from
    compute_idf() to make rarity a meaningful signal.

    `bridge_calculator` is optional. When None, the bridge signal
    stays at the legacy 0.5 stub (back-compat for tests + the
    legacy path). Pass a BridgeCalculator with a loaded Lightcast
    hierarchy to enable real proximity-based bridging (Spec B3
    TASK 3).
    """

    def __init__(
        self,
        inventory_skill_ids: set[str],
        idf: dict[str, float] | None = None,
        default_idf: float = 1.0,
        bridge_calculator=None,
    ) -> None:
        self.inventory = set(inventory_skill_ids)
        self.idf = idf
        self.default_idf = default_idf
        self.bridge_calc = bridge_calculator

    def _coverage(self, posting: set[str]) -> float:
        if not posting:
            return 0.0
        return len(self.inventory & posting) / len(posting)

    def _rarity(self, posting: set[str]) -> float:
        if not posting:
            return 0.0
        if self.idf is None:
            # Stub: equal weighting => same as coverage. Spec B2.
            return self._coverage(posting)
        matched = self.inventory & posting
        idf_matched = sum(self.idf.get(s, self.default_idf) for s in matched)
        idf_total = sum(self.idf.get(s, self.default_idf) for s in posting)
        if idf_total <= 0:
            return 0.0
        return idf_matched / idf_total

    def _bridge(self, posting: set[str]) -> float:
        if not posting:
            return 0.0
        if self.bridge_calc is None:
            return _BRIDGE_STUB_VALUE
        return self.bridge_calc.bridge_score(posting, self.inventory)

    def score(self, posting_skill_ids) -> ScoringResult:
        """Score a single posting against the inventory.

        posting_skill_ids: any iterable of skill IDs (deduped via
        set() internally). May be a list (legacy flat JSON shape)
        or a set.
        """
        posting = set(posting_skill_ids)
        coverage = self._coverage(posting)
        rarity = self._rarity(posting)
        bridge = self._bridge(posting)
        overall = (
            _COEF_COVERAGE * coverage
            + _COEF_RARITY * rarity
            + _COEF_BRIDGE * bridge
        ) * 10.0
        return ScoringResult(
            coverage=coverage,
            rarity=rarity,
            bridge=bridge,
            overall=overall,
            tier=_assign_tier(overall),
            matched=self.inventory & posting,
            missed=posting - self.inventory,
            posting_count=len(posting),
            inventory_count=len(self.inventory),
        )
