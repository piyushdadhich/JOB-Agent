"""v2.3 evaluator pipeline orchestrator.

Wires Stage 2a (deterministic) -> Stage 2pre (Gemma 3 4B filter)
-> Stage 2c-score (Gemma 4 E4B decomposed scoring) -> Stage 2c-counter
(Gemma 4 E4B factored verification, conditional on provisional
STRONG/TOP_TIER) -> deterministic Python combine.

Two execution modes:

  - evaluate_posting(): one posting end-to-end. Used by tests and
    ad-hoc scripts.
  - run_batch(): two-phase batch over many postings. Phase 1 filters
    everything with the small Gemma 3 4B (VRAM-resident on this 4 GB
    Pascal GPU). Phase 2 unloads the filter and decides only the
    PROCEEDs with Gemma 4 E4B. One model swap total, not N.

Architectural rules enforced here (do not break):
  - Stage 2c-counter runs ONLY when provisional tier is STRONG or
    TOP_TIER. EXPLORATORY/SKIP get no counter call.
  - Stage 2c-counter receives ONLY the 4 numeric/bool prior scores;
    it never sees the evidence quotes or disqualifier_reason from
    the scoring call. (This invariant is enforced inside
    stage_2c_counter; pipeline just hands it the right dict.)
  - Filter and decide LLMClients are kept separate. Mixing models
    on this 4 GB card is not possible.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

import requests

from llm.client import LLMClient

from .profile_config import ProfileConfig
from .score import CombinedScore, combine
from .stage2a import Stage2a, Stage2aResult
from .stage2c_counter import Stage2CCounterResult, stage_2c_counter
from .stage2c_score import Stage2CScoreResult, stage_2c_score
from .stage2pre import Stage2PreResult, stage_2pre

logger = logging.getLogger(__name__)

FILTER_MODEL = "gemma3-4b-ctx4k"
DECIDE_MODEL = "gemma4:e4b"


@dataclass(frozen=True)
class PipelineVerdict:
    tier: str
    skip_at: Optional[str]               # "2a" | "2pre" | None
    skip_reason: Optional[str]
    pre_result: Optional[Stage2PreResult]
    score_result: Optional[Stage2CScoreResult]
    counter_result: Optional[Stage2CCounterResult]
    combined: Optional[CombinedScore]
    latency_total_ms: int
    latency_breakdown: dict


def _empty_breakdown() -> dict:
    return {
        "2a_ms": 0,
        "2pre_ms": 0,
        "2c_score_ms": 0,
        "2c_counter_ms": 0,
    }


def _skip_verdict(
    stage: str,
    reason: Optional[str],
    pre_result: Optional[Stage2PreResult],
    latency_total_ms: int,
    breakdown: dict,
) -> PipelineVerdict:
    return PipelineVerdict(
        tier="SKIP",
        skip_at=stage,
        skip_reason=reason,
        pre_result=pre_result,
        score_result=None,
        counter_result=None,
        combined=None,
        latency_total_ms=latency_total_ms,
        latency_breakdown=breakdown,
    )


def _build_proceed_verdict(
    pre: Stage2PreResult,
    score_result: Stage2CScoreResult,
    counter_result: Optional[Stage2CCounterResult],
    final: CombinedScore,
    latency_total_ms: int,
) -> PipelineVerdict:
    breakdown = {
        "2a_ms": 0,
        "2pre_ms": pre.latency_ms,
        "2c_score_ms": score_result.latency_ms,
        "2c_counter_ms": (
            counter_result.latency_ms if counter_result else 0
        ),
    }
    return PipelineVerdict(
        tier=final.tier,
        skip_at=None,
        skip_reason=None,
        pre_result=pre,
        score_result=score_result,
        counter_result=counter_result,
        combined=final,
        latency_total_ms=latency_total_ms,
        latency_breakdown=breakdown,
    )


def _run_decide(
    posting: dict,
    inventory_summary: str,
    profile_config: ProfileConfig,
    llm_decide: LLMClient,
) -> tuple[Stage2CScoreResult, Optional[Stage2CCounterResult], CombinedScore]:
    """Run the 2c-score and (conditional) 2c-counter calls and return
    (score_result, counter_result, final_combined).
    """
    score_result = stage_2c_score(
        posting, inventory_summary, profile_config, llm_decide,
    )
    provisional = combine(score_result, counter_result=None)

    counter_result: Optional[Stage2CCounterResult] = None
    if provisional.tier in ("STRONG", "TOP_TIER"):
        counter_result = stage_2c_counter(
            posting, inventory_summary, profile_config, llm_decide,
            prior_scores={
                "function_score": score_result.function_score,
                "domain_score": score_result.domain_score,
                "seniority_score": score_result.seniority_score,
                "disqualifier_present": (
                    score_result.disqualifier_present
                ),
            },
        )

    final = combine(score_result, counter_result)
    return score_result, counter_result, final


def evaluate_posting(
    posting: dict,
    inventory_summary: str,
    profile_config: ProfileConfig,
    stage2a: Stage2a,
    llm_filter: LLMClient,
    llm_decide: LLMClient,
) -> PipelineVerdict:
    """Run the full v2.3 pipeline on one posting end-to-end."""
    t_start = time.time()

    # Stage 2a (deterministic)
    s2a = stage2a.evaluate(posting)
    if s2a.result != Stage2aResult.PASS_TO_2B:
        elapsed_ms = int((time.time() - t_start) * 1000)
        breakdown = _empty_breakdown()
        breakdown["2a_ms"] = elapsed_ms
        return _skip_verdict(
            stage="2a",
            reason=s2a.rule_fired,
            pre_result=None,
            latency_total_ms=elapsed_ms,
            breakdown=breakdown,
        )

    # Stage 2pre (filter)
    pre = stage_2pre(
        posting, inventory_summary, profile_config, llm_filter,
    )
    if pre.verdict == "SKIP":
        elapsed_ms = int((time.time() - t_start) * 1000)
        breakdown = _empty_breakdown()
        breakdown["2pre_ms"] = pre.latency_ms
        return _skip_verdict(
            stage="2pre",
            reason=pre.skip_reason,
            pre_result=pre,
            latency_total_ms=elapsed_ms,
            breakdown=breakdown,
        )

    # Stage 2c-score + conditional 2c-counter -> combine
    score_result, counter_result, final = _run_decide(
        posting, inventory_summary, profile_config, llm_decide,
    )
    elapsed_ms = int((time.time() - t_start) * 1000)
    return _build_proceed_verdict(
        pre, score_result, counter_result, final, elapsed_ms,
    )


def _unload_model(host: str, model: str) -> None:
    """Best-effort model unload. Used between filter and decide
    phases — the 4 GB GPU cannot hold both simultaneously.
    """
    try:
        requests.post(
            f"{host}/api/generate",
            json={"model": model, "prompt": "", "keep_alive": 0},
            timeout=30,
        )
    except Exception as e:
        logger.warning(
            "model unload of %s failed (best-effort): %s", model, e,
        )


def run_batch(
    postings: list[dict],
    inventory_summary: str,
    profile_config: ProfileConfig,
    stage2a: Stage2a,
    llm_filter: Optional[LLMClient] = None,
    llm_decide: Optional[LLMClient] = None,
) -> dict[int, PipelineVerdict]:
    """Two-phase batch evaluation.

    Phase 1: Stage 2a + Stage 2pre on every posting using llm_filter.
    Phase 2: Stage 2c-score + conditional Stage 2c-counter on the
             PROCEED postings using llm_decide.

    The filter model is unloaded between phases so the decide
    model fits in the 4 GB VRAM budget.
    """
    if llm_filter is None:
        llm_filter = LLMClient(model=FILTER_MODEL)
    if llm_decide is None:
        llm_decide = LLMClient(model=DECIDE_MODEL)

    verdicts: dict[int, PipelineVerdict] = {}
    proceed_with_pre: list[tuple[dict, Stage2PreResult]] = []

    # --- Phase 1: filter ---
    for posting in postings:
        pid = int(posting["id"])
        t_start = time.time()

        s2a = stage2a.evaluate(posting)
        if s2a.result != Stage2aResult.PASS_TO_2B:
            elapsed_ms = int((time.time() - t_start) * 1000)
            breakdown = _empty_breakdown()
            breakdown["2a_ms"] = elapsed_ms
            verdicts[pid] = _skip_verdict(
                stage="2a",
                reason=s2a.rule_fired,
                pre_result=None,
                latency_total_ms=elapsed_ms,
                breakdown=breakdown,
            )
            continue

        pre = stage_2pre(
            posting, inventory_summary, profile_config, llm_filter,
        )
        if pre.verdict == "SKIP":
            elapsed_ms = int((time.time() - t_start) * 1000)
            breakdown = _empty_breakdown()
            breakdown["2pre_ms"] = pre.latency_ms
            verdicts[pid] = _skip_verdict(
                stage="2pre",
                reason=pre.skip_reason,
                pre_result=pre,
                latency_total_ms=elapsed_ms,
                breakdown=breakdown,
            )
            continue

        proceed_with_pre.append((posting, pre))

    # Free VRAM between phases.
    _unload_model(llm_filter.host, FILTER_MODEL)

    # --- Phase 2: decide ---
    for posting, pre in proceed_with_pre:
        pid = int(posting["id"])
        t_start = time.time()
        try:
            score_result, counter_result, final = _run_decide(
                posting, inventory_summary, profile_config, llm_decide,
            )
        except Exception as e:
            logger.exception(
                "decide stage raised for posting %s; recording as "
                "EXPLORATORY/coerced", pid,
            )
            elapsed_ms = int((time.time() - t_start) * 1000)
            breakdown = _empty_breakdown()
            breakdown["2pre_ms"] = pre.latency_ms
            verdicts[pid] = PipelineVerdict(
                tier="EXPLORATORY",
                skip_at=None,
                skip_reason=f"decide_exception: {type(e).__name__}",
                pre_result=pre,
                score_result=None,
                counter_result=None,
                combined=None,
                latency_total_ms=elapsed_ms + pre.latency_ms,
                latency_breakdown=breakdown,
            )
            continue

        elapsed_ms = int((time.time() - t_start) * 1000)
        verdicts[pid] = _build_proceed_verdict(
            pre, score_result, counter_result, final,
            elapsed_ms + pre.latency_ms,
        )

    return verdicts
