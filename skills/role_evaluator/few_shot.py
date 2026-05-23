"""Few-shot example selector for the Stage 2b prompt.

The Stage 2b LLM is given a small set of human-labeled examples
(one TOP_TIER, one EXPLORATORY, one SKIP) so it can ground its
verdicts in the candidate's actual prior judgments. This module
selects those examples deterministically from the eval_labels table.

Properties:

  - Reproducible: same current_posting_id always yields the same
    selection (seed = sha256(posting_id)). Re-running Stage 2b on
    the same posting will reuse the same examples.
  - Leave-one-out: the current posting is excluded from the pool
    so we don't accidentally feed a posting back to itself as a
    few-shot example.
  - Per-tier balanced: exactly k_per_tier examples per tier,
    output ordered TOP_TIER -> EXPLORATORY -> SKIP (the order the
    Stage 2b prompt expects).
  - Graceful: if a tier has no eligible examples (e.g. EXPLORATORY
    pool is empty in the current data), that tier is silently
    omitted from the output and a warning is logged. The caller
    decides what to do with fewer-than-3 examples.

Tier mapping:
  eval_labels.verdict only carries 'shortlist' / 'skip' / 'unsure'
  values; there is no human-side TOP_TIER vs EXPLORATORY split. We
  map 'shortlist' -> TOP_TIER and 'skip' -> SKIP, leaving
  EXPLORATORY empty until a future schema or labeling pass adds
  the distinction.
"""
from __future__ import annotations

import hashlib
import logging
import random
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

TIERS = ("TOP_TIER", "EXPLORATORY", "SKIP")
_TRUNCATE_AT = 500


@dataclass(frozen=True)
class FewShotExample:
    posting_id: int
    title: str
    employer: str
    posting_text: str
    posting_text_truncated: str
    human_tier: str
    human_reasoning: str


def _truncate(text: str) -> str:
    if not text:
        return ""
    if len(text) <= _TRUNCATE_AT:
        return text
    return text[:_TRUNCATE_AT] + "..."


def _verdict_to_tier(verdict: str) -> Optional[str]:
    if verdict == "shortlist":
        return "TOP_TIER"
    if verdict == "skip":
        return "SKIP"
    return None


def _seed_int(posting_id: int) -> int:
    digest = hashlib.sha256(str(posting_id).encode()).hexdigest()
    return int(digest[:16], 16)


class FewShotSelector:
    """Selects few-shot examples from the eval_labels table."""

    def __init__(self, tracker) -> None:
        self._tracker = tracker
        self._cache: Optional[dict[str, list[FewShotExample]]] = None
        self._undersized_warned: set[tuple[str, int]] = set()

    def _load_pool(self) -> dict[str, list[FewShotExample]]:
        if self._cache is not None:
            return self._cache

        pool: dict[str, list[FewShotExample]] = {
            tier: [] for tier in TIERS
        }
        for label in self._tracker.list_eval_labels():
            tier = _verdict_to_tier(label["verdict"])
            if tier is None:
                continue
            opp = self._tracker.get_opportunity_by_id(
                label["opportunity_id"],
            )
            if opp is None:
                continue
            text = opp.get("posting_text") or ""
            reason = label.get("reason") or label.get("notes") or ""
            example = FewShotExample(
                posting_id=label["opportunity_id"],
                title=opp.get("title") or "",
                employer=opp.get("employer") or "",
                posting_text=text,
                posting_text_truncated=_truncate(text),
                human_tier=tier,
                human_reasoning=reason,
            )
            pool[tier].append(example)

        # Warn once per process about empty tiers — pool is cached so
        # this block only runs on the first _load_pool() call.
        for tier in TIERS:
            if not pool[tier]:
                logger.warning(
                    "FewShotSelector: tier %s pool is empty; "
                    "selections will omit this tier",
                    tier,
                )

        self._cache = pool
        return pool

    def select(
        self,
        current_posting_id: int,
        k_per_tier: int = 1,
    ) -> list[FewShotExample]:
        rng = random.Random(_seed_int(current_posting_id))
        pool = self._load_pool()

        out: list[FewShotExample] = []
        for tier in TIERS:
            eligible = [
                e for e in pool[tier]
                if e.posting_id != current_posting_id
            ]
            if not eligible:
                continue

            # v2.12: in the SKIP tier, prefer up to 3 user_flagged
            # examples (strongest negative signals) before sampling
            # general SKIPs. Auto-skipped postings at
            # evaluator_version='rule-skip-v1' live ONLY in
            # eval_decisions, never in eval_labels, so they don't
            # enter the pool here at all.
            if tier == "SKIP":
                flagged = [
                    e for e in eligible
                    if e.human_reasoning == "user_flagged"
                ]
                non_flagged = [
                    e for e in eligible
                    if e.human_reasoning != "user_flagged"
                ]
                picked: list[FewShotExample] = []
                picked.extend(flagged[:min(3, k_per_tier)])
                remaining = k_per_tier - len(picked)
                if remaining > 0 and non_flagged:
                    picked.extend(rng.sample(
                        non_flagged,
                        min(remaining, len(non_flagged)),
                    ))
                out.extend(picked)
                continue

            if len(eligible) < k_per_tier:
                key = (tier, k_per_tier)
                if key not in self._undersized_warned:
                    logger.warning(
                        "FewShotSelector: tier %s has only %d "
                        "eligible examples but k_per_tier=%d; "
                        "returning all available",
                        tier,
                        len(eligible),
                        k_per_tier,
                    )
                    self._undersized_warned.add(key)
            picked = rng.sample(
                eligible, min(k_per_tier, len(eligible)),
            )
            out.extend(picked)
        return out
