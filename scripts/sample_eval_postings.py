"""Phase 6.0.3: Sample 20 additional postings for labeling.

Picks 20 postings NOT in the existing top-30, weighted to give
variety:
  - 5 from zero bucket (overlap = 0)
  - 5 from low bucket (overlap 1-3)
  - 10 from high bucket but ranked OUTSIDE the top-30

Generates scripts/output/eval_extension_20.md in the same format
as shortlist_review.md (checkboxes for each).

Reproducible with random.seed(42).

Diagnostic. Runs in venv\\.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.matching.scorer import SCORER_VERSION
from engine.persistence.tracker import Tracker

OUTPUT_PATH = Path("scripts/output/eval_extension_20.md")
SEED = 42
N_ZERO = 5
N_LOW = 5
N_HIGH_OUTSIDE_TOP30 = 10
TOP_N = 30  # excluded from high-bucket sampling

# Total target for the eval extension. If zero-bucket pool is
# smaller than N_ZERO, the deficit is redistributed to low-bucket
# so the total sample size stays at 20.
N_TOTAL_TARGET = 20


def _format_skill_list(labels: list[str], limit: int = 10) -> str:
    if not labels:
        return "_(none)_"
    out = labels[:limit]
    bullet = "\n".join(f"  - {s}" for s in out)
    if len(labels) > limit:
        bullet += f"\n  - _... and {len(labels) - limit} more_"
    return bullet


def _format_top_matches(matches: list, limit: int = 5) -> str:
    if not matches:
        return "_(none)_"
    lines = []
    for m in matches[:limit]:
        if not isinstance(m, dict):
            continue
        skill = m.get("posting_skill", "?")
        reason = m.get("reason", "")
        lines.append(f"  - **{skill}** -- {reason}")
    return "\n".join(lines) if lines else "_(none)_"


def main() -> None:
    random.seed(SEED)

    tracker = Tracker("default")
    try:
        rows = tracker.list_match_scores(scorer_version=SCORER_VERSION)
        # Bucket pools
        zero = [r for r in rows if r["bucket"] == "zero"]
        low = [r for r in rows if r["bucket"] == "low"]
        high = [r for r in rows if r["bucket"] == "high"]
        # Sort high by (gemma_score DESC, coverage_idf DESC) and
        # exclude top-30 (those are already in shortlist_review.md).
        high.sort(
            key=lambda r: (
                -(r.get("gemma_score") or -999),
                -(r.get("coverage_idf") or 0.0),
            ),
        )
        high_outside = high[TOP_N:]

        print(f"Pools: zero={len(zero)}, low={len(low)}, "
              f"high_outside_top30={len(high_outside)}")

        # Take all zero up to N_ZERO; redistribute deficit to low.
        actual_zero = min(N_ZERO, len(zero))
        zero_deficit = N_ZERO - actual_zero
        actual_low = min(N_LOW + zero_deficit, len(low))
        actual_high = min(
            N_HIGH_OUTSIDE_TOP30, len(high_outside)
        )

        sample_zero = random.sample(zero, actual_zero)
        sample_low = random.sample(low, actual_low)
        sample_high = random.sample(high_outside, actual_high)

        sample = sample_zero + sample_low + sample_high
        print(f"Sampled {len(sample)} postings: "
              f"{len(sample_zero)} zero + {len(sample_low)} low "
              f"+ {len(sample_high)} high")
        if zero_deficit:
            print(f"  (zero pool short by {zero_deficit}; "
                  f"redistributed to low bucket)")

        # Pre-load skill labels for overlap rendering.
        out_lines: list[str] = []

        def emit(line: str = "") -> None:
            out_lines.append(line)

        emit("# Phase 6.0 -- Eval set extension (20 postings)")
        emit("")
        emit(
            "These 20 postings extend the 30 already-labeled "
            "shortlist into a 50-case eval set used to measure "
            "Phase 6 recall."
        )
        emit("")
        emit(f"Sample composition (seed={SEED}):")
        emit(f"- {len(sample_zero)} from `zero` bucket "
             f"(overlap=0)")
        emit(f"- {len(sample_low)} from `low` bucket "
             f"(overlap 1-3)")
        emit(f"- {len(sample_high)} from `high` bucket but "
             f"ranked OUTSIDE the top-30")
        emit("")
        emit("## Instructions")
        emit("")
        emit("For each posting below, mark ONE checkbox by "
             "replacing `- [ ]` with `- [x]`:")
        emit("- **Would shortlist** -- you'd seriously apply.")
        emit("- **Would skip** -- not a fit, don't apply.")
        emit("- **Unsure** -- need more info, on the fence.")
        emit("")
        emit("Save the file when done.")
        emit("")
        emit("---")
        emit("")

        for i, r in enumerate(sample, 1):
            opp_id = r["opportunity_id"]
            opp = tracker.get_opportunity_by_id(opp_id)
            if opp is None:
                continue
            employer = opp.get("employer", "?")
            title = opp.get("title", "?")
            location = opp.get("location", "")
            url = opp.get("url", "")

            overlap_labels_map = tracker.get_skill_labels(
                r["overlap_skill_ids"]
            )
            overlap_labels = sorted(
                {v for v in overlap_labels_map.values()
                 if v != "<unknown>"}
            )

            emit(f"## {i}. {employer} -- {title}")
            emit("")
            emit(f"- **Bucket:** `{r['bucket']}`")
            emit(f"- **Location:** {location or '_n/a_'}")
            emit(f"- **Source URL:** {url}")
            emit(f"- **opp_id:** `{opp_id}`")
            emit("")
            emit("**Deterministic scores:**")
            emit(f"- coverage_idf: `{r['coverage_idf']:.3f}`")
            emit(f"- coverage_raw: `{r['coverage_raw']:.3f}`")
            emit(f"- overlap: "
                 f"`{r['overlap_count']}` / "
                 f"`{r['posting_skill_count']}` posting skills")
            emit("")
            if r.get("gemma_score") is not None:
                emit(
                    f"**Gemma score: `{r['gemma_score']}`** / 10"
                )
                emit("")
                summary = r.get("gemma_summary") or ""
                if summary:
                    emit(f"> {summary}")
                    emit("")
                emit("**Gemma top matches:**")
                emit(_format_top_matches(
                    r.get("gemma_top_matches") or []
                ))
                emit("")
            else:
                emit("**Gemma score:** _(not run -- "
                     "low/zero bucket)_")
                emit("")

            emit("**Overlap skills:**")
            emit(_format_skill_list(overlap_labels))
            emit("")
            emit("**Verdict:**")
            emit("- [ ] Would shortlist")
            emit("- [ ] Would skip")
            emit("- [ ] Unsure")
            emit("")
            emit("---")
            emit("")
    finally:
        tracker.close()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text("\n".join(out_lines), encoding="utf-8")
    print(f"Written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
