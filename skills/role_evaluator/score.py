"""v2.3 score combine: deterministic Python.

Takes a Stage2CScoreResult plus an optional Stage2CCounterResult
and produces a CombinedScore with raw / normalized / tier /
components.

Weighting: function * 3 + domain * 2 + seniority * 2.
Disqualifier halves raw. Substantive counter-argument downgrades
tier by one when it is STRONG or TOP_TIER (EXPLORATORY and SKIP
are not affected).

Tier thresholds 80 / 60 / 35 are placeholders; tune in Step 5
fast eval against the 30-posting set.

The legacy combine(stage2a_verdict, stage2b_verdict) lives in
evaluator.py under the name _legacy_combine for the pre-v2.3
fallback path.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .stage2c_counter import Stage2CCounterResult
    from .stage2c_score import Stage2CScoreResult


# 3*3 + 3*2 + 3*2 — perfect score across all three dimensions.
MAX_RAW = 21

THRESHOLD_TOP_TIER = 80
THRESHOLD_STRONG = 60
THRESHOLD_EXPLORATORY = 35

# Counter-argument downgrade is gated by score robustness. A STRONG
# verdict with normalized >= STRONG_DOWNGRADE_FLOOR is treated as
# "confidently strong" — the score itself is high enough that a
# substantive counter is more likely a domain-specific concern the
# candidate can bridge than a hard mismatch the score missed.
# Iter 3 fast eval surfaced this: posting #219 (raw=14 = 66.7%)
# was downgraded by a counter calling out a domain gap the candidate
# could plausibly bridge.
STRONG_DOWNGRADE_FLOOR = 65


@dataclass(frozen=True)
class CombinedScore:
    raw: float
    normalized: float
    tier: str
    components: dict


def _initial_tier(normalized: float) -> str:
    if normalized >= THRESHOLD_TOP_TIER:
        return "TOP_TIER"
    if normalized >= THRESHOLD_STRONG:
        return "STRONG"
    if normalized >= THRESHOLD_EXPLORATORY:
        return "EXPLORATORY"
    return "SKIP"


def combine(
    score_result: "Stage2CScoreResult",
    counter_result: "Optional[Stage2CCounterResult]",
) -> CombinedScore:
    raw = (
        score_result.function_score * 3
        + score_result.domain_score * 2
        + score_result.seniority_score * 2
    )
    if score_result.disqualifier_present:
        raw = raw / 2

    normalized = (raw / MAX_RAW) * 100
    tier = _initial_tier(normalized)

    if counter_result and counter_result.counter_is_substantive:
        if tier == "TOP_TIER":
            tier = "STRONG"
        elif tier == "STRONG" and normalized < STRONG_DOWNGRADE_FLOOR:
            tier = "EXPLORATORY"
        # EXPLORATORY and SKIP are not affected.
        # STRONG with normalized >= STRONG_DOWNGRADE_FLOOR also
        # not affected - see STRONG_DOWNGRADE_FLOOR docstring.

    return CombinedScore(
        raw=raw,
        normalized=normalized,
        tier=tier,
        components={
            "function": score_result.function_score,
            "domain": score_result.domain_score,
            "seniority": score_result.seniority_score,
            "disqualifier": score_result.disqualifier_present,
            "counter_substantive": (
                counter_result.counter_is_substantive
                if counter_result else None
            ),
        },
    )
