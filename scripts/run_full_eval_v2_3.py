"""Full 290-posting eval for the v2.3 two-stage filter+decide pipeline.

Usage:
  python scripts\\run_full_eval_v2_3.py --profile default
  python scripts\\run_full_eval_v2_3.py --profile default --limit 50
  python scripts\\run_full_eval_v2_3.py --profile default --resume

Calls pipeline.run_batch (two-phase: filter all with gemma3-4b-ctx4k,
unload, decide PROCEEDs with gemma4:e4b). Persists verdicts to:
  - eval_decisions table (evaluator_version='pipeline-v2.3.0')
  - stage_decisions table (per-stage audit trail)
  - scripts/output/eval_verdicts_pipeline_v2_3_<YYYY-MM-DD>.jsonl
  - scripts/output/eval_report_pipeline_v2_3_<YYYYMMDDTHHMMSSZ>.md

Resume: re-running on the same day with --resume reuses today's
JSONL and skips postings already evaluated. Crash recovery friendly.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.evaluator.grading import (
    build_culture_signals,
    build_interview_plan,
    detect_red_flags,
    is_top_grade,
    score_to_letter,
)
from engine.llm.call_manager import EVALUATOR_VERSION as CLOUD_EVALUATOR_VERSION
from engine.persistence.tracker import Tracker
from llm.client import LLMClient
from skills.inventory import InventoryTool
from skills.role_evaluator.pipeline import (
    DECIDE_MODEL,
    FILTER_MODEL,
    PipelineVerdict,
    run_batch,
)
from skills.role_evaluator.profile_config import ProfileConfig
from skills.role_evaluator.stage2a import Stage2a, Stage2aResult

EVALUATOR_VERSION = "pipeline-v2.3.0"

# Sibling versions in the v2.3 evaluator family. Postings whose
# latest eval has any of these are NOT re-evaluated by
# find_postings_needing_eval.
PREFILTER_ONLY_VERSION = "stage-2pre-only-v2_3"
FALLBACK_VERSION = "pipeline-v2.3.0-fallback"

CURRENT_EVALUATOR_VERSIONS = frozenset({
    EVALUATOR_VERSION,
    CLOUD_EVALUATOR_VERSION,
    PREFILTER_ONLY_VERSION,
    FALLBACK_VERSION,
    "rule-skip-v1",      # v2.12: auto-skipped at persist by flag_rule
})

DEFAULT_TOP_K = 30
SHORTLIST_LABEL = "shortlist"
OUTPUT_DIR = PROJECT_ROOT / "scripts" / "output"

logger = logging.getLogger(__name__)


# --- JSONL serialization -----------------------------------------

def verdict_to_jsonl_record(pid: int, v: PipelineVerdict) -> dict:
    s = v.score_result
    c = v.counter_result
    p = v.pre_result
    return {
        "posting_id": pid,
        "evaluator_version": EVALUATOR_VERSION,
        "tier": v.tier,
        "skip_at": v.skip_at,
        "skip_reason": v.skip_reason,
        "normalized": v.combined.normalized if v.combined else None,
        "raw": v.combined.raw if v.combined else None,
        "components": v.combined.components if v.combined else None,
        "function_score": s.function_score if s else None,
        "function_evidence": (s.function_evidence[:200] if s else None),
        "domain_score": s.domain_score if s else None,
        "domain_evidence": (s.domain_evidence[:200] if s else None),
        "seniority_score": s.seniority_score if s else None,
        "seniority_evidence": (s.seniority_evidence[:200] if s else None),
        "disqualifier_present": (
            s.disqualifier_present if s else None
        ),
        "disqualifier_reason": (
            s.disqualifier_reason[:300] if s and s.disqualifier_reason
            else None
        ),
        "counter_text": (
            c.strongest_argument_against[:300] if c else None
        ),
        "counter_substantive": c.counter_is_substantive if c else None,
        "pre_verdict": p.verdict if p else None,
        "pre_skip_reason": p.skip_reason if p else None,
        "latency_total_ms": v.latency_total_ms,
        "latency_breakdown": v.latency_breakdown,
        "prompt_eval_ms": {
            "2pre": p.prompt_eval_duration_ms if p else 0,
            "2c_score": s.prompt_eval_duration_ms if s else 0,
            "2c_counter": c.prompt_eval_duration_ms if c else 0,
        },
    }


def _read_existing_verdicts(path: Path) -> dict[int, dict]:
    if not path.exists():
        return {}
    out: dict[int, dict] = {}
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                out[int(rec["posting_id"])] = rec
            except (ValueError, KeyError, json.JSONDecodeError):
                logger.warning("skipping malformed JSONL line in %s", path)
    return out


# --- DB persistence ----------------------------------------------

def _build_stage_trace_dict(v: PipelineVerdict) -> dict:
    trace = {"stages_run": [], "tier": v.tier}
    if v.skip_at == "2a":
        trace["stages_run"].append({
            "stage": "stage2a",
            "decision": "REJECT",
            "reason": v.skip_reason,
        })
    elif v.skip_at == "2pre":
        trace["stages_run"].append(
            {"stage": "stage2a", "decision": "PASS", "reason": None}
        )
        trace["stages_run"].append({
            "stage": "stage2pre",
            "decision": "SKIP",
            "reason": v.skip_reason,
        })
    else:
        trace["stages_run"].append(
            {"stage": "stage2a", "decision": "PASS", "reason": None}
        )
        if v.pre_result:
            trace["stages_run"].append({
                "stage": "stage2pre",
                "decision": v.pre_result.verdict,
                "reason": v.pre_result.skip_reason,
            })
        if v.score_result:
            s = v.score_result
            trace["stages_run"].append({
                "stage": "stage2c_score",
                "function_score": s.function_score,
                "domain_score": s.domain_score,
                "seniority_score": s.seniority_score,
                "disqualifier_present": s.disqualifier_present,
            })
        if v.counter_result:
            trace["stages_run"].append({
                "stage": "stage2c_counter",
                "substantive": v.counter_result.counter_is_substantive,
            })
        if v.combined:
            trace["normalized"] = round(v.combined.normalized, 1)
            trace["components"] = v.combined.components
    return trace


def _build_reasoning(v: PipelineVerdict) -> str:
    if v.skip_at == "2a":
        return f"[stage2a] {v.skip_reason}"
    if v.skip_at == "2pre":
        return f"[stage2pre] {v.skip_reason}"
    if v.score_result is None:
        return f"[skip_reason] {v.skip_reason or 'unknown'}"
    s = v.score_result
    bits = [
        f"fn={s.function_score}",
        f"dom={s.domain_score}",
        f"sen={s.seniority_score}",
    ]
    if s.disqualifier_present:
        bits.append(f"disq={(s.disqualifier_reason or '')[:120]}")
    if v.counter_result and v.counter_result.counter_is_substantive:
        bits.append(
            f"counter={v.counter_result.strongest_argument_against[:120]}"
        )
    if v.combined:
        bits.append(f"norm={v.combined.normalized:.1f}")
    return "; ".join(bits)


def persist_verdict(
    tracker, pid: int, v: PipelineVerdict,
    *,
    evaluator_version: str = EVALUATOR_VERSION,
    posting: Optional[dict] = None,
    company: Optional[dict] = None,
    interview_plan: Optional[list] = None,
    culture_signals: Optional[list] = None,
) -> None:
    """Write eval_decisions + stage_decisions rows for one verdict.

    evaluator_version defaults to EVALUATOR_VERSION (canonical local).
    The cloud-fallback path overrides to FALLBACK_VERSION so the
    eval_decisions row is distinguishable from a clean local run.

    posting / company power the deterministic red-flag detector and
    the letter-grade mapping (Spec JA-1 TASK 2). interview_plan and
    culture_signals come in pre-built from the caller because they
    are expensive Gemma calls gated on letter_grade in {A, B}.
    """
    fit_score: Optional[int] = None
    if v.combined is not None:
        fit_score = max(1, min(10, round(v.combined.normalized / 10)))

    # Letter grade is computed off the normalized 0-10 score, not the
    # rounded fit_score, so the A/B boundary at 8.0 lines up with the
    # spec exactly.
    norm = v.combined.normalized if v.combined else None
    letter_grade = score_to_letter(norm)

    red_flags = None
    if posting is not None:
        red_flags = detect_red_flags(posting, company)

    tracker.record_evaluation(
        opportunity_id=pid,
        evaluator_version=evaluator_version,
        tier=v.tier,
        fit_score=fit_score,
        sector=None,
        role_type=None,
        stage_trace=_build_stage_trace_dict(v),
        reasoning=_build_reasoning(v),
        letter_grade=letter_grade,
        interview_plan=interview_plan,
        red_flags=red_flags,
        culture_signals=culture_signals,
    )

    # Per-stage audit trail.
    if v.skip_at == "2a":
        tracker.insert_stage_decision(
            opportunity_id=pid,
            stage_name="stage2a",
            stage_version=evaluator_version,
            decision="REJECT",
            reason=v.skip_reason,
            metadata=None,
        )
        return

    tracker.insert_stage_decision(
        opportunity_id=pid,
        stage_name="stage2a",
        stage_version=evaluator_version,
        decision="PASS",
        reason=None,
        metadata=None,
    )
    if v.pre_result is not None:
        tracker.insert_stage_decision(
            opportunity_id=pid,
            stage_name="stage2pre",
            stage_version=evaluator_version,
            decision=v.pre_result.verdict,
            reason=v.pre_result.skip_reason,
            metadata={"latency_ms": v.pre_result.latency_ms},
        )
    if v.skip_at == "2pre":
        return
    if v.score_result is not None:
        s = v.score_result
        tracker.insert_stage_decision(
            opportunity_id=pid,
            stage_name="stage2c_score",
            stage_version=evaluator_version,
            decision=v.tier,
            reason=None,
            metadata={
                "function_score": s.function_score,
                "function_evidence": s.function_evidence[:300],
                "domain_score": s.domain_score,
                "seniority_score": s.seniority_score,
                "disqualifier_present": s.disqualifier_present,
                "latency_ms": s.latency_ms,
            },
        )
    if v.counter_result is not None:
        c = v.counter_result
        tracker.insert_stage_decision(
            opportunity_id=pid,
            stage_name="stage2c_counter",
            stage_version=evaluator_version,
            decision=("SUBSTANTIVE" if c.counter_is_substantive
                      else "NOT_SUBSTANTIVE"),
            reason=c.strongest_argument_against[:300],
            metadata={"latency_ms": c.latency_ms},
        )


# --- Metrics + report --------------------------------------------

def compute_metrics(verdicts: list[dict], labels: dict, top_k: int) -> dict:
    if not verdicts:
        return {
            "precision_at_k": 0.0, "recall_on_shortlist": 0.0,
            "top_k": top_k, "confusion_matrix": {},
            "tier_distribution": {}, "skip_stage_distribution": {},
            "total_evaluated": 0, "total_labeled": 0,
        }

    # Sort by fit_score (which mirrors normalized) DESC for ranking;
    # tier > normalized > posting_id for stable tiebreak.
    def _rank_key(v: dict) -> tuple:
        norm = v.get("normalized") or 0
        tier_rank = {"TOP_TIER": 4, "STRONG": 3, "EXPLORATORY": 2,
                     "SKIP": 1}.get(v.get("tier"), 0)
        return (-tier_rank, -float(norm), int(v["posting_id"]))

    sorted_v = sorted(verdicts, key=_rank_key)
    top = sorted_v[:top_k]

    shortlist_ids = {
        pid for pid, verdict in labels.items()
        if verdict == SHORTLIST_LABEL
    }
    top_in_shortlist = sum(
        1 for v in top if int(v["posting_id"]) in shortlist_ids
    )
    precision_at_k = top_in_shortlist / top_k if top_k else 0.0
    recall = (top_in_shortlist / len(shortlist_ids)
              if shortlist_ids else 0.0)

    confusion: dict[str, dict[str, int]] = {}
    for v in verdicts:
        pid = int(v["posting_id"])
        if pid not in labels:
            continue
        predicted = v.get("tier") or "UNKNOWN"
        actual = labels[pid]
        confusion.setdefault(predicted, {})
        confusion[predicted][actual] = (
            confusion[predicted].get(actual, 0) + 1
        )

    tier_dist = Counter(v.get("tier") for v in verdicts)
    skip_dist = Counter(
        v.get("skip_at") for v in verdicts if v.get("skip_at")
    )

    return {
        "precision_at_k": precision_at_k,
        "recall_on_shortlist": recall,
        "top_k": top_k,
        "confusion_matrix": confusion,
        "tier_distribution": dict(tier_dist),
        "skip_stage_distribution": dict(skip_dist),
        "total_evaluated": len(verdicts),
        "total_labeled": len(labels),
    }


def format_report(
    metrics: dict, run_at: str, wall_seconds: float,
    filter_rate: float, cache_signal: str,
    sum_2pre_ms: int, sum_score_ms: int, sum_counter_ms: int,
    counter_runs: int, postings_decided: int,
) -> str:
    lines: list[str] = []
    lines.append("# v2.3 Pipeline Eval Report")
    lines.append("")
    lines.append(f"- Run at: {run_at}")
    lines.append(f"- Evaluator version: {EVALUATOR_VERSION}")
    lines.append(f"- Total evaluated: {metrics['total_evaluated']}")
    lines.append(f"- Total labeled: {metrics['total_labeled']}")
    lines.append(f"- Top K: {metrics['top_k']}")
    lines.append(
        f"- Wall clock: {wall_seconds:.0f}s ({wall_seconds/60:.1f} min)"
    )
    lines.append("")
    lines.append("## Headline metrics")
    lines.append("")
    lines.append(
        f"- **Precision @ top-{metrics['top_k']}**: "
        f"{metrics['precision_at_k']:.3f} "
        f"(target >= 0.30; v2.1 baseline 0.167)"
    )
    lines.append(
        f"- **Recall on shortlist**: "
        f"{metrics['recall_on_shortlist']:.3f} "
        f"(target >= 0.33)"
    )
    lines.append("")
    lines.append("## Pipeline distribution")
    lines.append("")
    for stage, n in metrics["skip_stage_distribution"].items():
        lines.append(f"- Skipped at {stage}: {n}")
    lines.append(f"- Decided at 2c: {postings_decided}")
    lines.append(f"- Filter rate at 2pre: {filter_rate:.1f}% of 2a-passed")
    lines.append(
        f"- Counter calls (STRONG/TOP_TIER provisional): {counter_runs}"
    )
    lines.append("")
    lines.append("## Tier distribution")
    lines.append("")
    for tier, n in metrics["tier_distribution"].items():
        lines.append(f"- {tier}: {n}")
    lines.append("")
    lines.append("## Latency breakdown")
    lines.append("")
    lines.append(f"- Sum 2pre time: {sum_2pre_ms/1000:.0f}s")
    lines.append(f"- Sum 2c-score time: {sum_score_ms/1000:.0f}s")
    lines.append(f"- Sum 2c-counter time: {sum_counter_ms/1000:.0f}s")
    lines.append(f"- Prefix cache (E4B): {cache_signal}")
    lines.append("")
    lines.append("## Confusion matrix (predicted_tier x human_verdict)")
    lines.append("")
    cm = metrics["confusion_matrix"]
    if cm:
        actual_keys = sorted({a for row in cm.values() for a in row.keys()})
        header = "| predicted \\ human | " + " | ".join(actual_keys) + " |"
        sep = "|---" * (len(actual_keys) + 1) + "|"
        lines.append(header)
        lines.append(sep)
        for pred in sorted(cm.keys()):
            row = cm[pred]
            cells = [str(row.get(a, 0)) for a in actual_keys]
            lines.append(f"| {pred} | " + " | ".join(cells) + " |")
    else:
        lines.append("(no labeled postings)")
    lines.append("")
    return "\n".join(lines)


# --- Driver ------------------------------------------------------

# --- Spec JA-1-fix T1 helpers ------------------------------------
#
# build_interview_plan + build_culture_signals were merged in JA-1
# but never wired into the eval loop. The persist_verdict signature
# accepts them, but nothing was computing them. These helpers + the
# `_run_extras_for_verdict` call below close that loop.

_PROOF_POINTS_PATH = (
    PROJECT_ROOT / "source_materials" / "default" / "proof_points.md"
)


def _extract_matched_skills(v: PipelineVerdict) -> list[str]:
    """Pull up to 5 matched skill names off the verdict's combined
    object. The current v2.3 pipeline doesn't yet populate this,
    so we accept an empty result and let the prompt say 'n/a'."""
    combined = getattr(v, "combined", None)
    if combined is None:
        return []
    bag: list = []
    for attr in ("matched_skills", "function_evidence"):
        val = getattr(combined, attr, None)
        if isinstance(val, list):
            bag.extend(val)
    return [str(s) for s in bag[:5] if s]


def _extract_gap_skills(v: PipelineVerdict) -> list[str]:
    """Same idea as _extract_matched_skills but for gaps."""
    combined = getattr(v, "combined", None)
    if combined is None:
        return []
    val = getattr(combined, "gap_skills", None)
    if isinstance(val, list):
        return [str(s) for s in val[:3] if s]
    return []


def _load_proof_points_for_eval() -> list[str]:
    """Read source_materials/default/proof_points.md once, cache the
    flat list of metric strings. Returns [] when the file is
    missing (the inventory is PII-gated so it's expected to be
    absent in some CI environments)."""
    cache = getattr(_load_proof_points_for_eval, "_cache", None)
    if cache is not None:
        return cache
    points: list[str] = []
    if _PROOF_POINTS_PATH.exists():
        for raw in _PROOF_POINTS_PATH.read_text(
            encoding="utf-8",
        ).splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "|" not in line:
                continue
            head, _, metric = line.partition("|")
            head = head.strip()
            metric = metric.strip()
            # Skip the header row 'Format: [ROLE] | [SKILL_...]'.
            if " " in head or not metric:
                continue
            points.append(metric)
    _load_proof_points_for_eval._cache = points
    return points


def _build_ollama_call(llm: LLMClient):
    """Adapt LLMClient.generate to the (prompt: str) -> str shape
    that engine.evaluator.grading expects."""
    def _call(prompt: str) -> str:
        return llm.generate(prompt, temperature=0.0)
    return _call


def _run_extras_for_verdict(
    tracker: Tracker,
    pid: int,
    v: PipelineVerdict,
    *,
    ollama_call,
) -> dict:
    """Compute the four v2.20 extras for one verdict.

    Returns a dict with letter_grade / posting / company / red_flags
    / interview_plan / culture_signals. Interview plan + culture
    signals only run for A/B grades (the spec gate). Any LLM-side
    failure collapses to None for that field — never raises.
    """
    norm = v.combined.normalized if v.combined else None
    letter_grade = score_to_letter(norm)
    posting = tracker.get_opportunity_by_id(pid)
    company = None
    if posting and posting.get("company_id"):
        company = tracker.get_company_by_id(int(posting["company_id"]))
    red_flags = (
        detect_red_flags(posting or {}, company) if posting else None
    )
    interview_plan = None
    culture_signals = None
    if is_top_grade(letter_grade) and posting is not None:
        try:
            interview_plan = build_interview_plan(
                posting=posting,
                matched_skills=_extract_matched_skills(v),
                gap_skills=_extract_gap_skills(v),
                proof_points=_load_proof_points_for_eval(),
                ollama_call=ollama_call,
            ) or None
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "interview_plan failed for posting %s: %s", pid, exc,
            )
            interview_plan = None
        try:
            culture_signals = build_culture_signals(
                posting=posting, ollama_call=ollama_call,
            ) or None
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "culture_signals failed for posting %s: %s", pid, exc,
            )
            culture_signals = None
    return {
        "letter_grade": letter_grade,
        "posting": posting,
        "company": company,
        "red_flags": red_flags,
        "interview_plan": interview_plan,
        "culture_signals": culture_signals,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="default")
    parser.add_argument("--limit", type=int, default=None,
                        help="cap postings (smoke runs).")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_at = datetime.now(timezone.utc).isoformat()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    verdicts_path = (
        OUTPUT_DIR / f"eval_verdicts_pipeline_v2_3_{today}.jsonl"
    )
    report_path = (
        OUTPUT_DIR / f"eval_report_pipeline_v2_3_{timestamp}.md"
    )

    if verdicts_path.exists() and not args.resume:
        print(
            f"ERROR: today's verdicts file already exists at "
            f"{verdicts_path}.\nPass --resume to continue, or "
            f"delete the file to start fresh."
        )
        return 1

    tracker = Tracker(args.profile)
    try:
        inv = InventoryTool(args.profile)
        profile_config = ProfileConfig(args.profile)
        inventory_summary = inv.get_summary()
        stage2a = Stage2a(inv, profile_config)

        all_postings = tracker.list_opportunities(
            limit=args.limit or 1000,
        )
        existing = (
            _read_existing_verdicts(verdicts_path)
            if args.resume else {}
        )
        if existing:
            logger.info(
                "resume: %d posting verdicts already on disk", len(existing),
            )
        remaining = [
            p for p in all_postings
            if int(p["id"]) not in existing
        ]
        logger.info(
            "evaluating %d postings (skipping %d already done)",
            len(remaining), len(existing),
        )

        labels_rows = tracker.list_eval_labels()
        labels = {r["opportunity_id"]: r["verdict"] for r in labels_rows}

        # --- Run pipeline -----------------------------------------
        llm_filter = LLMClient(model=FILTER_MODEL)
        llm_decide = LLMClient(model=DECIDE_MODEL)

        t_start = time.time()
        new_verdicts: dict = {}
        if remaining:
            new_verdicts = run_batch(
                remaining, inventory_summary, profile_config, stage2a,
                llm_filter=llm_filter, llm_decide=llm_decide,
            )
        wall = time.time() - t_start

        # Persist + append JSONL.
        # Spec JA-1-fix T1: compute the four v2.20 extras here so
        # they actually land on the eval_decisions row. A/B grades
        # burn two Gemma calls (interview_plan + culture_signals);
        # everything else gets letter_grade + red_flags only.
        extras_ollama_call = _build_ollama_call(llm_decide)
        with verdicts_path.open("a", encoding="utf-8") as f:
            for pid, v in new_verdicts.items():
                try:
                    extras = _run_extras_for_verdict(
                        tracker, pid, v,
                        ollama_call=extras_ollama_call,
                    )
                    persist_verdict(
                        tracker, pid, v,
                        posting=extras["posting"],
                        company=extras["company"],
                        interview_plan=extras["interview_plan"],
                        culture_signals=extras["culture_signals"],
                    )
                except Exception:
                    logger.exception("persist failed for posting %s", pid)
                rec = verdict_to_jsonl_record(pid, v)
                f.write(json.dumps(rec) + "\n")
                f.flush()
                existing[pid] = rec

        # --- Aggregate metrics -----------------------------------
        all_records = list(existing.values())
        metrics = compute_metrics(all_records, labels, args.top_k)

        # filter rate + per-stage timing
        passed_2a = sum(
            1 for r in all_records if r.get("skip_at") != "2a"
        )
        skipped_2pre = sum(
            1 for r in all_records if r.get("skip_at") == "2pre"
        )
        filter_rate = (
            skipped_2pre / passed_2a * 100 if passed_2a else 0.0
        )
        decided = sum(
            1 for r in all_records if r.get("skip_at") is None
        )
        sum_2pre = sum(
            (r.get("latency_breakdown") or {}).get("2pre_ms", 0)
            for r in all_records
        )
        sum_score = sum(
            (r.get("latency_breakdown") or {}).get("2c_score_ms", 0)
            for r in all_records
        )
        sum_counter = sum(
            (r.get("latency_breakdown") or {}).get("2c_counter_ms", 0)
            for r in all_records
        )
        counter_runs = sum(
            1 for r in all_records
            if r.get("counter_substantive") is not None
        )

        decide_prompt_evals = [
            (r.get("prompt_eval_ms") or {}).get("2c_score", 0)
            for r in all_records
            if (r.get("prompt_eval_ms") or {}).get("2c_score", 0) > 0
        ]
        cache_signal = "n/a"
        if len(decide_prompt_evals) >= 2:
            first = decide_prompt_evals[0]
            rest = decide_prompt_evals[1:]
            rest_avg = sum(rest) / len(rest)
            cache_signal = (
                f"first={first}ms, rest_avg={rest_avg:.0f}ms, "
                f"ratio={rest_avg/first:.3f}"
            )

        report = format_report(
            metrics, run_at, wall, filter_rate, cache_signal,
            sum_2pre, sum_score, sum_counter, counter_runs, decided,
        )
        report_path.write_text(report, encoding="utf-8")

        bar = "=" * 70
        print()
        print(bar)
        print("FULL EVAL COMPLETE")
        print(bar)
        print(f"Profile:           {args.profile}")
        print(f"Evaluator:         {EVALUATOR_VERSION}")
        print(f"Total evaluated:   {metrics['total_evaluated']}")
        print(f"Total labeled:     {metrics['total_labeled']}")
        print(
            f"Precision @ top-{args.top_k}: "
            f"{metrics['precision_at_k']:.3f} "
            f"(target >= 0.30, v2.1 baseline 0.167)"
        )
        print(
            f"Recall on shortlist: {metrics['recall_on_shortlist']:.3f} "
            f"(target >= 0.33)"
        )
        print(
            f"Filter rate at 2pre: {filter_rate:.1f}% of 2a-passed"
        )
        print(f"Counter runs:      {counter_runs}")
        print(f"Wall clock:        {wall:.0f}s ({wall/60:.1f} min)")
        print(f"Prefix cache:      {cache_signal}")
        print(f"Report:            {report_path}")
        print(f"Verdicts:          {verdicts_path}")
        print(bar)
    finally:
        tracker.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
