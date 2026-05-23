"""Phase 5d Step 5c: Generate top-30 labeled shortlist for review.

Produces scripts/output/shortlist_review.md with top-30 postings
ranked by (gemma_score DESC, coverage_idf DESC). Each entry has:
  - Employer / title / location / source URL
  - Deterministic scores (coverage_raw, coverage_idf, bucket)
  - Gemma score + summary (when available)
  - Top matched skills (labels), transferable skills, gaps
  - Three checkboxes: Would shortlist / Would skip / Unsure

Alex fills in the checkboxes manually. Then
scripts/measure_shortlist_review.py reads the marked file and
reports precision / agreement.

Postings without a gemma_score (low / zero buckets) are NOT
included by default -- the top-30 fills first from
gemma-scored 'high' bucket. If fewer than 30 are scored, falls
back to deterministic-only order.

Diagnostic tool. Runs in venv\\.

Note: filename is generate_shortlist_review.py (not
generate_shortlist.py) because the latter is the Phase 4 daily
shortlist generator, a separate concern.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.matching.scorer import SCORER_VERSION
from engine.persistence.tracker import Tracker

OUTPUT_PATH = Path("scripts/output/shortlist_review.md")
TOP_N = 30


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


def _format_transferable(transfers: list, limit: int = 3) -> str:
    if not transfers:
        return "_(none)_"
    lines = []
    for t in transfers[:limit]:
        if not isinstance(t, dict):
            continue
        skill = t.get("posting_skill", "?")
        bridge = t.get("bridged_from", "?")
        reason = t.get("reason", "")
        lines.append(
            f"  - **{skill}** (from `{bridge}`) -- {reason}"
        )
    return "\n".join(lines) if lines else "_(none)_"


def _format_gaps(gaps: list, limit: int = 3) -> str:
    if not gaps:
        return "_(none)_"
    return "\n".join(f"  - {g}" for g in gaps[:limit])


def main() -> None:
    tracker = Tracker("default")
    try:
        rows = tracker.list_match_scores(
            scorer_version=SCORER_VERSION, bucket="high",
        )
        # Sort: gemma_score DESC (NULL last), coverage_idf DESC.
        rows.sort(
            key=lambda r: (
                -(r.get("gemma_score") or -999),
                -(r.get("coverage_idf") or 0.0),
            ),
        )
        top = rows[:TOP_N]
        print(f"Top {len(top)} postings selected from "
              f"{len(rows)} 'high'-bucket rows.")

        out_lines: list[str] = []

        def emit(line: str = "") -> None:
            out_lines.append(line)

        emit("# Phase 5d Step 5c -- Shortlist review")
        emit("")
        emit(f"Top {len(top)} postings ranked by "
             f"(gemma_score DESC, coverage_idf DESC).")
        emit("")
        emit(f"Scorer version: `{SCORER_VERSION}`")
        emit("Generated from `data/default/tracker.db` "
             "match_scores rows.")
        emit("")
        emit("## Instructions")
        emit("")
        emit("For each posting below, mark ONE of the three "
             "checkboxes by replacing `- [ ]` with `- [x]`:")
        emit("- **Would shortlist** -- you'd seriously apply.")
        emit("- **Would skip** -- not a fit, don't apply.")
        emit("- **Unsure** -- need more info, on the fence.")
        emit("")
        emit("If you spot postings OUTSIDE this top-30 that you "
             "would have wanted to see here, list them at the "
             "bottom under '## Missing'.")
        emit("")
        emit("---")
        emit("")

        for i, r in enumerate(top, 1):
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
            emit(f"- bucket: `{r['bucket']}`")
            emit("")
            if r.get("gemma_score") is not None:
                emit(
                    f"**Gemma score: "
                    f"`{r['gemma_score']}`** / 10  "
                    f"(hallucination flags: "
                    f"`{r.get('gemma_hallucination_flags', 0)}`)"
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
                emit("**Transferable skills:**")
                emit(_format_transferable(
                    r.get("gemma_transferable") or []
                ))
                emit("")
                emit("**Critical gaps:**")
                emit(_format_gaps(
                    r.get("gemma_critical_gaps") or []
                ))
                emit("")
            else:
                emit("**Gemma score:** _(not run -- "
                     "low/zero bucket)_")
                emit("")

            emit("**All overlap skill labels:**")
            emit(_format_skill_list(overlap_labels))
            emit("")
            emit("**Verdict:**")
            emit("- [ ] Would shortlist")
            emit("- [ ] Would skip")
            emit("- [ ] Unsure")
            emit("")
            emit("---")
            emit("")

        emit("## Missing")
        emit("")
        emit("_If any postings outside the top-30 should have "
             "been here, list them below as_ "
             "`opp_id: employer -- title -- one-line reason`. "
             "_Leave blank if none._")
        emit("")
    finally:
        tracker.close()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        "\n".join(out_lines), encoding="utf-8",
    )
    print(f"Written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
