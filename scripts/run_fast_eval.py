"""v2.3 fast eval: run the pipeline against the 30-posting set
and check the three gates.

Reads tests/eval_set/default_fast_30.jsonl, fetches the postings
from the tracker, runs pipeline.run_batch with two-phase batching
(filter all 30 with Gemma 3 4B, then decide PROCEEDs with Gemma 4 E4B),
and writes a markdown report under scripts/output/.

  python scripts\\run_fast_eval.py                        # local (default)
  python scripts\\run_fast_eval.py --evaluator cloud      # Gemma 4 31B via Google AI Studio

Gates (per v2.3 spec):
  A. Over-rated SKIPs caught: >=7/10 drop to EXPLORATORY or SKIP
  B. Known-good shortlists preserved: >=8/10 stay STRONG or TOP_TIER
  C. Unambiguous SKIPs maintained: >=9/10 stay SKIP

All three must pass before we run the full 290 eval.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

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
from skills.role_evaluator.stage2a import Stage2a

PROFILE_ID = "default"
EVAL_SET_PATH = (
    PROJECT_ROOT / "tests" / "eval_set" / "default_fast_30.jsonl"
)
OUTPUT_DIR = PROJECT_ROOT / "scripts" / "output"

GATE_A_TARGET = 7   # over-rated SKIPs caught
GATE_B_TARGET = 8   # known-good shortlists preserved
GATE_C_TARGET = 9   # unambig SKIPs held


def _load_eval_set() -> list[dict]:
    out = []
    with EVAL_SET_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _verdict_passes(
    entry: dict, verdict: PipelineVerdict,
) -> bool:
    return verdict.tier in entry["expected_tier_range"]


def _format_components(verdict: PipelineVerdict) -> str:
    if not verdict.combined:
        return "(no combined score)"
    c = verdict.combined.components
    return (
        f"fn={c['function']} dom={c['domain']} sen={c['seniority']} "
        f"disq={c['disqualifier']} counter_sub={c['counter_substantive']}"
    )


def _format_counter(verdict: PipelineVerdict) -> str:
    if not verdict.counter_result:
        return "(no counter call)"
    text = verdict.counter_result.strongest_argument_against
    return text[:200] + ("..." if len(text) > 200 else "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evaluator", choices=["local", "cloud"], default="local",
        help="Evaluator backend (default: local).",
    )
    args = parser.parse_args()

    eval_set = _load_eval_set()
    print(f"Loaded {len(eval_set)} eval entries from {EVAL_SET_PATH.name}")

    tracker = Tracker(PROFILE_ID)
    try:
        # Fetch postings — preserve eval-set order so the report
        # groups entries by category cleanly.
        postings: list[dict] = []
        missing: list[int] = []
        for entry in eval_set:
            p = tracker.get_opportunity_by_id(entry["posting_id"])
            if not p:
                missing.append(entry["posting_id"])
                continue
            postings.append(p)
        if missing:
            print(f"WARNING: missing posting IDs: {missing}")

        if args.evaluator == "cloud":
            return _run_cloud_eval(eval_set, postings)

        return _run_local_eval(eval_set, postings)
    finally:
        tracker.close()


def _run_local_eval(eval_set, postings) -> int:
    inventory = InventoryTool(PROFILE_ID)
    profile_config = ProfileConfig(PROFILE_ID)
    inventory_summary = inventory.get_summary()
    stage2a = Stage2a(inventory, profile_config)

    print(f"Running LOCAL pipeline on {len(postings)} postings...")
    print(f"  filter model: {FILTER_MODEL}")
    print(f"  decide model: {DECIDE_MODEL}")
    print()

    llm_filter = LLMClient(model=FILTER_MODEL)
    llm_decide = LLMClient(model=DECIDE_MODEL)

    t_start = time.time()
    verdicts = run_batch(
        postings, inventory_summary, profile_config, stage2a,
        llm_filter=llm_filter, llm_decide=llm_decide,
    )
    elapsed = time.time() - t_start
    return _write_local_report_and_gate(
        eval_set, postings, verdicts, elapsed,
    )


def _write_local_report_and_gate(
    eval_set, postings, verdicts, elapsed,
) -> int:

    # --- Tally ----------------------------------------------------
    by_pid = {e["posting_id"]: e for e in eval_set}

    def _category_verdicts(name: str) -> list[tuple[dict, PipelineVerdict]]:
        return [
            (by_pid[pid], v)
            for pid, v in verdicts.items()
            if by_pid[pid]["category"] == name
        ]

    over_rated = _category_verdicts("over_rated_skip")
    known_good = _category_verdicts("known_good_shortlist")
    unambig = _category_verdicts("unambiguous_skip")

    caught = sum(
        1 for _, v in over_rated
        if v.tier in ("EXPLORATORY", "SKIP")
    )
    preserved = sum(
        1 for _, v in known_good
        if v.tier in ("STRONG", "TOP_TIER")
    )
    held = sum(1 for _, v in unambig if v.tier == "SKIP")

    gate_a = caught >= GATE_A_TARGET
    gate_b = preserved >= GATE_B_TARGET
    gate_c = held >= GATE_C_TARGET
    overall = gate_a and gate_b and gate_c

    # --- Stage timing --------------------------------------------
    skip_at_2a = sum(1 for v in verdicts.values() if v.skip_at == "2a")
    skip_at_2pre = sum(1 for v in verdicts.values() if v.skip_at == "2pre")
    proceed = sum(1 for v in verdicts.values() if v.skip_at is None)

    sum_2pre = sum(
        v.latency_breakdown.get("2pre_ms", 0) for v in verdicts.values()
    )
    sum_score = sum(
        v.latency_breakdown.get("2c_score_ms", 0) for v in verdicts.values()
    )
    sum_counter = sum(
        v.latency_breakdown.get("2c_counter_ms", 0)
        for v in verdicts.values()
    )

    counter_runs = sum(
        1 for v in verdicts.values() if v.counter_result is not None
    )

    # --- Prefix-cache signal -------------------------------------
    # Compare first decide call's prompt_eval to subsequent ones.
    decide_prompt_evals = [
        v.score_result.prompt_eval_duration_ms
        for v in verdicts.values()
        if v.score_result is not None
    ]
    cache_signal = "n/a"
    if len(decide_prompt_evals) >= 2:
        first = decide_prompt_evals[0]
        rest_avg = sum(decide_prompt_evals[1:]) / len(decide_prompt_evals[1:])
        if first > 0:
            ratio = rest_avg / first
            cache_signal = (
                f"first={first}ms, rest_avg={rest_avg:.0f}ms, "
                f"ratio={ratio:.3f}"
            )

    # --- Markdown report -----------------------------------------
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = OUTPUT_DIR / f"fast_eval_v2_3_{timestamp}.md"

    lines: list[str] = []
    lines.append("# v2.3 Fast Eval Report")
    lines.append("")
    lines.append(f"Run at: {datetime.now(timezone.utc).isoformat()}")
    lines.append(
        f"Wall clock: {elapsed:.0f}s ({elapsed/60:.1f} min) "
        f"on {len(postings)} postings"
    )
    lines.append(f"Filter model: {FILTER_MODEL}")
    lines.append(f"Decide model: {DECIDE_MODEL}")
    lines.append("")

    lines.append("## Gates")
    lines.append("")
    lines.append(
        f"- **Gate A** (over-rated SKIPs caught >= {GATE_A_TARGET}/10): "
        f"**{caught}/10** {'PASS' if gate_a else 'FAIL'}"
    )
    lines.append(
        f"- **Gate B** (known-good shortlists preserved >= {GATE_B_TARGET}/10): "
        f"**{preserved}/10** {'PASS' if gate_b else 'FAIL'}"
    )
    lines.append(
        f"- **Gate C** (unambig SKIPs held >= {GATE_C_TARGET}/10): "
        f"**{held}/10** {'PASS' if gate_c else 'FAIL'}"
    )
    lines.append("")
    lines.append(
        f"**Overall: {'PASS - proceed to Step 6' if overall else 'FAIL - tune prompts and re-run'}**"
    )
    lines.append("")

    lines.append("## Pipeline distribution")
    lines.append("")
    lines.append(f"- Skipped at 2a (deterministic): {skip_at_2a}/30")
    lines.append(f"- Skipped at 2pre (filter): {skip_at_2pre}/30")
    lines.append(f"- Proceeded to decide: {proceed}/30")
    lines.append(
        f"- Filter rate at 2pre: "
        f"{skip_at_2pre / max(1, 30 - skip_at_2a) * 100:.1f}% of 2a-passed"
    )
    lines.append(f"- Counter calls (STRONG/TOP_TIER provisional): {counter_runs}")
    lines.append("")

    lines.append("## Latency")
    lines.append("")
    lines.append(f"- Sum 2pre time: {sum_2pre/1000:.0f}s")
    lines.append(f"- Sum 2c-score time: {sum_score/1000:.0f}s")
    lines.append(f"- Sum 2c-counter time: {sum_counter/1000:.0f}s")
    lines.append(f"- Total wall: {elapsed:.0f}s")
    lines.append(f"- Prefix cache signal (E4B): {cache_signal}")
    lines.append("")

    def _table(category: str, rows: list[tuple[dict, PipelineVerdict]]) -> None:
        lines.append(f"## {category}")
        lines.append("")
        lines.append(
            "| pid | employer | title | tier | expected | "
            "PASS | components |"
        )
        lines.append(
            "|---|---|---|---|---|---|---|"
        )
        for entry, v in rows:
            posting = next(
                (p for p in postings if p["id"] == entry["posting_id"]),
                {},
            )
            tier = v.tier
            passed = _verdict_passes(entry, v)
            comps = _format_components(v) if v.combined else (
                f"(skip_at={v.skip_at}, reason={v.skip_reason})"
            )
            lines.append(
                f"| {entry['posting_id']} | "
                f"{(posting.get('employer') or '')[:25]} | "
                f"{(posting.get('title') or '')[:35]} | "
                f"{tier} | {' or '.join(entry['expected_tier_range'])} | "
                f"{'YES' if passed else 'no'} | {comps} |"
            )
        lines.append("")

    _table("Over-rated SKIPs", over_rated)
    _table("Known-good shortlists", known_good)
    _table("Unambiguous SKIPs", unambig)

    lines.append("## Per-posting detail")
    lines.append("")
    for entry in eval_set:
        pid = entry["posting_id"]
        if pid not in verdicts:
            continue
        v = verdicts[pid]
        posting = next(
            (p for p in postings if p["id"] == pid), {},
        )
        lines.append(
            f"### #{pid} - "
            f"{posting.get('employer', '')} - {posting.get('title', '')}"
        )
        lines.append(f"- Category: {entry['category']}")
        lines.append(f"- Expected: {entry['expected_tier_range']}")
        lines.append(f"- Actual tier: **{v.tier}**")
        lines.append(f"- skip_at: {v.skip_at}")
        if v.skip_reason:
            lines.append(f"- skip_reason: {v.skip_reason}")
        if v.combined:
            lines.append(
                f"- normalized: {v.combined.normalized:.1f}"
            )
            lines.append(f"- components: {_format_components(v)}")
        if v.score_result:
            lines.append(
                f"- function_evidence: "
                f"{v.score_result.function_evidence!r}"
            )
            lines.append(
                f"- domain_evidence: "
                f"{v.score_result.domain_evidence!r}"
            )
            lines.append(
                f"- seniority_evidence: "
                f"{v.score_result.seniority_evidence!r}"
            )
            if v.score_result.disqualifier_present:
                lines.append(
                    f"- disqualifier_reason: "
                    f"{v.score_result.disqualifier_reason!r}"
                )
        if v.counter_result:
            lines.append(f"- counter: {_format_counter(v)}")
            lines.append(
                f"- counter_is_substantive: "
                f"{v.counter_result.counter_is_substantive}"
            )
        lines.append(
            f"- latency: total={v.latency_total_ms}ms; "
            f"2pre={v.latency_breakdown.get('2pre_ms', 0)}ms, "
            f"score={v.latency_breakdown.get('2c_score_ms', 0)}ms, "
            f"counter={v.latency_breakdown.get('2c_counter_ms', 0)}ms"
        )
        lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")

    print()
    print("=" * 70)
    print(f"Gate A: {caught}/10 -> {'PASS' if gate_a else 'FAIL'}")
    print(f"Gate B: {preserved}/10 -> {'PASS' if gate_b else 'FAIL'}")
    print(f"Gate C: {held}/10 -> {'PASS' if gate_c else 'FAIL'}")
    print(f"Overall: {'PASS' if overall else 'FAIL'}")
    print(f"Wall clock: {elapsed/60:.1f} min")
    print(f"Filter rate at 2pre: "
          f"{skip_at_2pre / max(1, 30 - skip_at_2a) * 100:.1f}% of 2a-passed")
    print(f"Prefix cache signal: {cache_signal}")
    print(f"Report: {report_path}")
    print("=" * 70)
    return 0 if overall else 2


def _run_cloud_eval(eval_set, postings) -> int:
    """Run the eval set through the Gemma 4 31B cloud evaluator.

    Uses ~50 API calls out of the daily 1500 budget. Does NOT
    persist to the tracker — eval-set runs are validation, not
    production.
    """
    from engine.llm.gemma_cloud_client import GemmaCloudClient
    from skills.role_evaluator.cloud_pipeline import (
        EVALUATOR_VERSION,
        _load_compact_inventory,
        _load_profile_yaml,
        _load_prompt_template,
        evaluate_one,
    )

    profile_yaml = _load_profile_yaml(PROFILE_ID)
    prompt_template = _load_prompt_template()
    inventory_summary = _load_compact_inventory(PROFILE_ID, profile_yaml)

    inv_tool = InventoryTool(PROFILE_ID)
    profile_config = ProfileConfig(PROFILE_ID)
    stage2a = Stage2a(inv_tool, profile_config)
    client = GemmaCloudClient(profile_id=PROFILE_ID)

    print(f"Running CLOUD pipeline on {len(postings)} postings...")
    print(f"  model: {GemmaCloudClient.MODEL}")
    print(f"  evaluator_version: {EVALUATOR_VERSION}")
    print()

    t_start = time.time()
    verdicts = {}
    for i, posting in enumerate(postings):
        try:
            v = evaluate_one(
                posting, prompt_template, inventory_summary,
                client, stage2a,
            )
            verdicts[posting["id"]] = v
        except Exception as e:
            print(f"  [{i}/{len(postings)}] #{posting['id']} ERROR: {e}")
            continue
        if (i + 1) % 10 == 0:
            print(
                f"  [{i + 1}/{len(postings)}] "
                f"used today: {client.count_today()}"
            )
    elapsed = time.time() - t_start

    by_pid = {e["posting_id"]: e for e in eval_set}

    def _cat(name):
        return [
            (by_pid[pid], v)
            for pid, v in verdicts.items()
            if by_pid[pid]["category"] == name
        ]

    over_rated = _cat("over_rated_skip")
    known_good = _cat("known_good_shortlist")
    unambig = _cat("unambiguous_skip")
    caught = sum(
        1 for _, v in over_rated if v.tier in ("EXPLORATORY", "SKIP")
    )
    preserved = sum(
        1 for _, v in known_good if v.tier in ("STRONG", "TOP_TIER")
    )
    held = sum(1 for _, v in unambig if v.tier == "SKIP")
    gate_a = caught >= GATE_A_TARGET
    gate_b = preserved >= GATE_B_TARGET
    gate_c = held >= GATE_C_TARGET
    overall = gate_a and gate_b and gate_c

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = OUTPUT_DIR / f"fast_eval_cloud_{timestamp}.md"
    lines = [
        "# Cloud Evaluator Fast Eval Report",
        "",
        f"Run at: {datetime.now(timezone.utc).isoformat()}",
        f"Wall clock: {elapsed:.0f}s ({elapsed / 60:.1f} min) "
        f"on {len(postings)} postings",
        f"Model: {GemmaCloudClient.MODEL}",
        f"Evaluator version: {EVALUATOR_VERSION}",
        "",
        "## Gates",
        "",
        f"- **Gate A** (over-rated SKIPs caught >= {GATE_A_TARGET}/10): "
        f"**{caught}/10** {'PASS' if gate_a else 'FAIL'}",
        f"- **Gate B** (known-good shortlists preserved >= {GATE_B_TARGET}/10): "
        f"**{preserved}/10** {'PASS' if gate_b else 'FAIL'}",
        f"- **Gate C** (unambig SKIPs held >= {GATE_C_TARGET}/10): "
        f"**{held}/10** {'PASS' if gate_c else 'FAIL'}",
        "",
        f"**Overall: {'PASS' if overall else 'FAIL — iterate'}**",
        "",
        "## Per-posting detail",
        "",
    ]
    for entry in eval_set:
        pid = entry["posting_id"]
        if pid not in verdicts:
            continue
        v = verdicts[pid]
        posting = next((p for p in postings if p["id"] == pid), {})
        lines.extend([
            f"### #{pid} - "
            f"{posting.get('employer', '')} - "
            f"{posting.get('title', '')}",
            f"- Category: {entry['category']}",
            f"- Expected: {entry['expected_tier_range']}",
            f"- Actual tier: **{v.tier}**",
            f"- skip_at: {v.skip_at}, skip_reason: {v.skip_reason}",
            f"- fit_score: {v.fit_score}, normalized: {v.normalized}",
            f"- scores: {v.scores}",
            "",
        ])
    report_path.write_text("\n".join(lines), encoding="utf-8")

    print()
    print("=" * 70)
    print(f"Gate A: {caught}/10 -> {'PASS' if gate_a else 'FAIL'}")
    print(f"Gate B: {preserved}/10 -> {'PASS' if gate_b else 'FAIL'}")
    print(f"Gate C: {held}/10 -> {'PASS' if gate_c else 'FAIL'}")
    print(f"Overall: {'PASS' if overall else 'FAIL'}")
    print(f"Wall clock: {elapsed / 60:.1f} min")
    print(f"Calls used today: {client.count_today()}")
    print(f"Report: {report_path}")
    print("=" * 70)
    return 0 if overall else 2


if __name__ == "__main__":
    sys.exit(main())
