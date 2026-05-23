"""Cloud evaluator pipeline.

Plugs into run_daily.py via --evaluator cloud and run_fast_eval.py
via --evaluator cloud. Reuses Stage 2a hard filters (deterministic,
free) before the single cloud call. Score combine reuses the same
thresholds as the local pipeline so tiers are directly comparable.
"""
from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.llm.call_manager import CallManager, EVALUATOR_VERSION
from engine.llm.gemma_cloud_client import GemmaCloudClient
from skills.inventory import InventoryTool
from skills.role_evaluator.profile_config import ProfileConfig
from skills.role_evaluator.score import (
    MAX_RAW,
    THRESHOLD_EXPLORATORY,
    THRESHOLD_STRONG,
    THRESHOLD_TOP_TIER,
)
from skills.role_evaluator.stage2a import Stage2a, Stage2aResult

logger = logging.getLogger(__name__)

PROMPT_PATH = (
    PROJECT_ROOT / "skills" / "role_evaluator" / "prompts"
    / "gemma4_cloud_prompt.txt"
)


# --- verdict shape used by run_fast_eval --------------------------

@dataclass(frozen=True)
class CloudVerdict:
    tier: str
    skip_at: Optional[str]               # "2a" | None
    skip_reason: Optional[str]
    fit_score: Optional[int]             # 1-10 or None
    normalized: Optional[float]          # 0-100 or None
    scores: Optional[dict]               # function/domain/seniority/disqualifier
    raw_verdict: Optional[dict]          # full cloud JSON


# --- score → tier --------------------------------------------------

def _scores_to_tier_and_fit(scores: dict) -> tuple[str, int, float]:
    """Map cloud scores dict to (tier, fit_score 1-10, normalized 0-100).

    Uses the same combine math as the local pipeline so tiers are
    directly comparable. Disqualifier halves raw (matching local
    score.combine behavior).
    """
    raw = (
        scores["function"] * 3
        + scores["domain"] * 2
        + scores["seniority"] * 2
    )
    if scores.get("disqualifier"):
        raw = raw / 2
    normalized = (raw / MAX_RAW) * 100
    if normalized >= THRESHOLD_TOP_TIER:
        tier = "TOP_TIER"
    elif normalized >= THRESHOLD_STRONG:
        tier = "STRONG"
    elif normalized >= THRESHOLD_EXPLORATORY:
        tier = "EXPLORATORY"
    else:
        tier = "SKIP"
    fit_score = max(1, min(10, round(normalized / 10)))
    return tier, fit_score, normalized


def parsed_to_verdict(
    parsed: dict, skip_at: Optional[str] = None,
) -> CloudVerdict:
    """Turn a cloud JSON response into a CloudVerdict."""
    if parsed.get("verdict") == "SKIP":
        return CloudVerdict(
            tier="SKIP", skip_at=skip_at,
            skip_reason=parsed.get("skip_reason"),
            fit_score=None, normalized=None, scores=None,
            raw_verdict=parsed,
        )
    scores = parsed["scores"]
    tier, fit, norm = _scores_to_tier_and_fit(scores)
    return CloudVerdict(
        tier=tier, skip_at=None, skip_reason=None,
        fit_score=fit, normalized=norm, scores=scores,
        raw_verdict=parsed,
    )


def _stage2a_to_verdict(s2a_result) -> CloudVerdict:
    return CloudVerdict(
        tier="SKIP", skip_at="2a",
        skip_reason=s2a_result.rule_fired,
        fit_score=None, normalized=None, scores=None,
        raw_verdict=None,
    )


# --- persistence ---------------------------------------------------

def _persist_cloud_verdict(tracker, posting_id: int, parsed: dict) -> None:
    """Write eval_decisions row from a successful cloud parsed result."""
    verdict = parsed_to_verdict(parsed, skip_at=None)
    reasoning = json.dumps({
        "verdict": parsed.get("verdict"),
        "skip_reason": parsed.get("skip_reason"),
        "scores": parsed.get("scores"),
    })
    tracker.record_evaluation(
        opportunity_id=posting_id,
        evaluator_version=EVALUATOR_VERSION,
        tier=verdict.tier,
        fit_score=verdict.fit_score,
        sector=None, role_type=None,
        stage_trace={"cloud": parsed},
        reasoning=reasoning,
    )


def _persist_stage2a_skip(tracker, posting_id: int, s2a_result) -> None:
    """Write a SKIP@2a eval_decisions row so the posting won't return tomorrow."""
    tracker.record_evaluation(
        opportunity_id=posting_id,
        evaluator_version=EVALUATOR_VERSION,
        tier="SKIP",
        fit_score=None,
        sector=None, role_type=None,
        stage_trace={"stage2a": {"rule_fired": s2a_result.rule_fired}},
        reasoning=f"stage2a:{s2a_result.rule_fired}",
    )
    tracker.insert_stage_decision(
        opportunity_id=posting_id,
        stage_name="stage2a",
        stage_version=EVALUATOR_VERSION,
        decision="REJECT",
        reason=s2a_result.rule_fired,
        metadata=None,
    )


def _persist_prefilter_skip(tracker, posting_id: int, pre_result) -> None:
    """Write eval_decisions + stage_decisions for a Stage 2pre SKIP
    on the priority=2 stream (no cloud call burnt)."""
    from scripts.run_full_eval_v2_3 import PREFILTER_ONLY_VERSION
    tracker.record_evaluation(
        opportunity_id=posting_id,
        evaluator_version=PREFILTER_ONLY_VERSION,
        tier="SKIP",
        fit_score=None,
        sector=None, role_type=None,
        stage_trace={"stage2pre": {
            "verdict": pre_result.verdict,
            "skip_reason": pre_result.skip_reason,
            "latency_ms": pre_result.latency_ms,
        }},
        reasoning=f"local pre-filter skip: {pre_result.skip_reason}",
    )
    tracker.insert_stage_decision(
        opportunity_id=posting_id,
        stage_name="stage2pre",
        stage_version=PREFILTER_ONLY_VERSION,
        decision="SKIP",
        reason=pre_result.skip_reason,
        metadata={"latency_ms": pre_result.latency_ms},
    )


def _persist_fallback_verdict(tracker, posting_id: int, verdict) -> None:
    """Persist a local v2.3 PipelineVerdict tagged as fallback."""
    from scripts.run_full_eval_v2_3 import (
        FALLBACK_VERSION, persist_verdict,
    )
    persist_verdict(
        tracker, posting_id, verdict,
        evaluator_version=FALLBACK_VERSION,
    )


def _local_fallback_evaluate(
    posting: dict, *, inventory_summary: str, profile_config,
    stage2a, llm_filter, llm_decide,
):
    """Run the FULL v2.3 four-stage pipeline for one posting.
    Used when cloud returns 500/503."""
    from skills.role_evaluator.pipeline import evaluate_posting
    return evaluate_posting(
        posting, inventory_summary, profile_config, stage2a,
        llm_filter=llm_filter, llm_decide=llm_decide,
    )


# --- profile + prompt + inventory loading --------------------------

def _load_profile_yaml(profile_id: str) -> dict:
    path = (
        PROJECT_ROOT / "config" / "profiles" / f"{profile_id}.yaml"
    )
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _load_prompt_template() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _load_compact_inventory(profile_id: str, profile_yaml: dict) -> str:
    inv_path = (
        profile_yaml.get("cloud_evaluator", {}) or {}
    ).get(
        "inventory_path",
        f"data/{profile_id}/inventory_summary_compact.md",
    )
    return Path(inv_path).read_text(encoding="utf-8")


# --- evaluate_one (single posting; used by fast eval) -------------

def evaluate_one(
    posting: dict,
    prompt_template: str,
    inventory_summary: str,
    client: GemmaCloudClient,
    stage2a: Stage2a,
) -> CloudVerdict:
    """Stage 2a + single cloud call for one posting.

    Used by run_fast_eval.py --evaluator cloud. Does NOT persist.
    """
    s2a = stage2a.evaluate(posting)
    if s2a.result != Stage2aResult.PASS_TO_2B:
        return _stage2a_to_verdict(s2a)

    parsed = client.evaluate_posting(
        opportunity_id=posting["id"],
        employer=posting.get("employer", "") or "",
        title=posting.get("title", "") or "",
        location=posting.get("location", "") or "",
        posting_text=posting.get("posting_text", "") or "",
        inventory_summary=inventory_summary,
        prompt_template=prompt_template,
    )
    return parsed_to_verdict(parsed, skip_at=None)


# --- batch (production; used by run_daily) ------------------------

def run_batch_cloud(
    tracker, profile_id: str,
    auto: bool = True,
    limit: Optional[int] = None,
) -> dict:
    """Two-stage evaluator (Spec 5).

    AI postings (eval_priority=1) go DIRECT to cloud Gemma 4 31B.
    Non-AI postings (eval_priority=2) get a local Stage 2pre filter
    first to save cloud budget on obvious skips. When cloud returns
    500/503, the posting falls back to the FULL local v2.3 pipeline
    instead of being deferred to tomorrow.

    Persistence:
      Stage 2a reject              -> CLOUD_EVALUATOR_VERSION
      Stage 2pre SKIP (priority=2) -> PREFILTER_ONLY_VERSION
      Cloud success                -> CLOUD_EVALUATOR_VERSION
      Cloud 5xx -> local fallback  -> FALLBACK_VERSION
    """
    # Lazy imports avoid run_daily <-> cloud_pipeline cycle and keep
    # the pipeline modules optional at import time for tools that
    # only want to construct verdicts.
    from scripts.run_daily import find_postings_needing_eval
    from scripts.run_full_eval_v2_3 import CURRENT_EVALUATOR_VERSIONS
    from skills.role_evaluator.pipeline import (
        DECIDE_MODEL, FILTER_MODEL, _unload_model,
    )
    from skills.role_evaluator.stage2pre import stage_2pre
    from llm.client import LLMClient

    prompt_template = _load_prompt_template()
    profile_yaml = _load_profile_yaml(profile_id)
    inventory_summary = _load_compact_inventory(profile_id, profile_yaml)

    client = GemmaCloudClient(profile_id=profile_id)

    inv_tool = InventoryTool(profile_id)
    profile_config = ProfileConfig(profile_id)
    stage2a = Stage2a(inv_tool, profile_config)

    # Family-aware planner: postings already at any of the four
    # current versions are skipped.
    candidates = find_postings_needing_eval(
        tracker,
        current_versions=CURRENT_EVALUATOR_VERSIONS,
        limit=limit,
    )

    # Stage 2a hard pre-filter (deterministic, free).
    stage2a_passed: list[dict] = []
    stage2a_skipped = 0
    for posting in candidates:
        s2a = stage2a.evaluate(posting)
        if s2a.result != Stage2aResult.PASS_TO_2B:
            try:
                _persist_stage2a_skip(tracker, posting["id"], s2a)
            except Exception:
                logger.exception(
                    "stage2a persist failed for %s", posting["id"],
                )
            stage2a_skipped += 1
        else:
            stage2a_passed.append(posting)

    # Split by eval_priority. None (legacy / pre-v2.11 row) -> 2.
    ai_direct = [
        p for p in stage2a_passed
        if (p.get("eval_priority") or 2) == 1
    ]
    standard = [
        p for p in stage2a_passed
        if (p.get("eval_priority") or 2) != 1
    ]

    # Local Stage 2pre on the priority=2 stream. The Stage 2pre
    # prompt is empirically tuned for PROCEED bias -- DO NOT REWRITE.
    llm_filter = LLMClient(model=FILTER_MODEL)
    pre_passed: list[dict] = []
    prefilter_skipped = 0
    for posting in standard:
        try:
            pre = stage_2pre(
                posting, inventory_summary, profile_config, llm_filter,
            )
        except Exception:
            logger.exception(
                "stage_2pre raised for %s; failing open to PROCEED",
                posting["id"],
            )
            pre_passed.append(posting)
            continue
        if pre.verdict == "SKIP":
            try:
                _persist_prefilter_skip(tracker, posting["id"], pre)
            except Exception:
                logger.exception(
                    "prefilter persist failed for %s", posting["id"],
                )
            prefilter_skipped += 1
        else:
            pre_passed.append(posting)

    # Free filter-model VRAM before cloud calls.
    _unload_model(llm_filter.host, FILTER_MODEL)

    cloud_bound = ai_direct + pre_passed

    top_ids: list[int] = []

    def _find_fn() -> list[dict]:
        return cloud_bound

    def _get_fn(pid: int) -> Optional[dict]:
        return tracker.get_opportunity_by_id(pid)

    def _persist_fn(item: dict, parsed: dict) -> None:
        _persist_cloud_verdict(tracker, item["id"], parsed)
        verdict = parsed_to_verdict(parsed, skip_at=None)
        if verdict.tier in ("TOP_TIER", "STRONG"):
            top_ids.append(item["id"])

    fallback_clients: dict = {}

    def _on_cloud_5xx(item: dict, error: Exception) -> None:
        if "filter" not in fallback_clients:
            fallback_clients["filter"] = LLMClient(model=FILTER_MODEL)
            fallback_clients["decide"] = LLMClient(model=DECIDE_MODEL)
        verdict = _local_fallback_evaluate(
            item,
            inventory_summary=inventory_summary,
            profile_config=profile_config,
            stage2a=stage2a,
            llm_filter=fallback_clients["filter"],
            llm_decide=fallback_clients["decide"],
        )
        _persist_fallback_verdict(tracker, item["id"], verdict)
        if verdict.tier in ("TOP_TIER", "STRONG"):
            top_ids.append(item["id"])
        logger.info(
            "Cloud 5xx fallback for %s: tier=%s",
            item["id"], verdict.tier,
        )

    mgr = CallManager(
        profile_id=profile_id,
        gemma_client=client,
        find_postings_fn=_find_fn,
        get_posting_fn=_get_fn,
        persist_fn=_persist_fn,
        prompt_template=prompt_template,
        inventory_summary=inventory_summary,
        on_cloud_5xx=_on_cloud_5xx,
    )
    inventory_dict = mgr.inventory()
    plan_dict = mgr.plan(inventory_dict)
    plan_dict["priority_1_count"] = len(ai_direct)
    plan_dict["priority_2_count_post_prefilter"] = len(pre_passed)
    plan_dict["prefilter_skipped"] = prefilter_skipped
    results = mgr.execute(plan_dict, auto=auto)
    print(mgr.report(results, plan_dict))

    # Free fallback VRAM if it was spun up.
    if fallback_clients:
        _unload_model(fallback_clients["filter"].host, FILTER_MODEL)
        _unload_model(fallback_clients["decide"].host, DECIDE_MODEL)

    auto_prompts_generated = 0
    if top_ids:
        try:
            from engine.resume.auto_prompt import auto_generate_prompts
            outcome = auto_generate_prompts(
                tracker, top_ids, profile_id=profile_id,
            )
            auto_prompts_generated = outcome["generated"]
            logger.info(
                "Auto-generated %d prompt pairs (skipped %d existing, "
                "%d missing) for shortlisted postings",
                outcome["generated"],
                outcome["skipped_existing"],
                outcome["skipped_missing"],
            )
        except Exception:
            logger.exception("auto_prompt generation failed (non-fatal)")

    return {
        "evaluator": "two-stage-v2_3",
        "evaluator_version": EVALUATOR_VERSION,
        "candidates": len(candidates),
        "stage2a_skipped": stage2a_skipped,
        "prefilter_skipped": prefilter_skipped,
        "priority_1_direct": len(ai_direct),
        "priority_2_after_prefilter": len(pre_passed),
        "evaluated": results["evaluated"],
        "proceeded": results["proceeded"],
        "skipped_by_model": results["skipped_by_model"],
        "fallback_to_local": results.get("fallback_to_local", 0),
        "failed": results["failed"],
        "deferred_new": plan_dict.get("deferred_new", 0),
        "deferred_retries": plan_dict.get("deferred_retries", 0),
        "auto_prompts_generated": auto_prompts_generated,
    }
