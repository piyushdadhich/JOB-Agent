"""Phase 5d Step 5b: Gemma contextual scoring.

For every match_scores row in the 'high' bucket where
gemma_score IS NULL, look up labels from skill_labels, build
the Gemma prompt, call Ollama, validate, and update the
gemma_* fields on the match_scores row.

Re-runnable: skips rows that already have gemma_score set.
On parse/HTTP failure, logs and skips (does NOT abort the
batch). Skipped postings can be retried by re-running.

Flags:
  --limit N        Process only the first N candidates
                   (default: all). Used for the kill-switch
                   10-posting test.
  --rate-limit-sec FLOAT
                   Sleep between Gemma calls (default 1.0).
  --print-each     Print Gemma's parsed output for each posting
                   as it's scored (used by kill-switch run).

Runs in venv\\ (no ML deps; HTTP-only to Ollama).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import requests

from engine.matching.gemma_scorer import (
    build_prompt,
    call_gemma,
    validate_gemma_response,
)
from engine.matching.scorer import SCORER_VERSION
from engine.persistence.tracker import Tracker


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--rate-limit-sec", type=float, default=1.0)
    p.add_argument("--print-each", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    tracker = Tracker("default")
    try:
        rows = tracker.list_match_scores(
            scorer_version=SCORER_VERSION, bucket="high",
        )
        candidates = [r for r in rows if r.get("gemma_score") is None]
        print(
            f"high-bucket rows: {len(rows)}, "
            f"already-scored: {len(rows) - len(candidates)}, "
            f"candidates: {len(candidates)}"
        )
        if args.limit is not None:
            candidates = candidates[: args.limit]
            print(f"Limited to first {len(candidates)}")

        # Pre-load inventory labels once (same for every posting).
        inv_row = tracker.get_profile_skills("default", "lightcast")
        inv_ids = list(set(inv_row["skill_ids"])) if inv_row else []
        inv_labels_map = tracker.get_skill_labels(inv_ids)
        inventory_labels = sorted({
            v for v in inv_labels_map.values()
            if v != "<unknown>"
        })

        scored = 0
        skipped: list[dict] = []
        score_dist: Counter[int] = Counter()
        hallucination_total = 0

        for i, row in enumerate(candidates, 1):
            opp_id = row["opportunity_id"]
            opp = tracker.get_opportunity_by_id(opp_id)
            if opp is None:
                skipped.append({
                    "opp_id": opp_id, "reason": "opportunity not found",
                })
                continue

            # Posting-level skill labels.
            posting_ids = json.loads(opp["extracted_skill_ids"])
            posting_id_set = list(set(posting_ids))
            posting_labels_map = tracker.get_skill_labels(posting_id_set)
            posting_labels = sorted({
                v for v in posting_labels_map.values()
                if v != "<unknown>"
            })

            # Overlap labels (from deterministic scorer's
            # overlap_skill_ids).
            overlap_labels_map = tracker.get_skill_labels(
                row["overlap_skill_ids"]
            )
            overlap_labels = sorted({
                v for v in overlap_labels_map.values()
                if v != "<unknown>"
            })

            prompt = build_prompt(
                posting_title=opp.get("title", "?"),
                posting_employer=opp.get("employer", "?"),
                posting_labels=posting_labels,
                inventory_labels=inventory_labels,
                overlap_labels=overlap_labels,
            )

            try:
                parsed, raw = call_gemma(prompt)
            except (ValueError, requests.exceptions.RequestException) as e:
                skipped.append({
                    "opp_id": opp_id,
                    "employer": opp.get("employer", "?"),
                    "title": opp.get("title", "?"),
                    "reason": f"{type(e).__name__}: {str(e)[:200]}",
                })
                if args.rate_limit_sec > 0:
                    time.sleep(args.rate_limit_sec)
                continue

            flags = validate_gemma_response(
                parsed, set(overlap_labels)
            )
            score = parsed.get("score")
            if isinstance(score, int):
                score_dist[score] += 1
            hallucination_total += flags

            tracker.update_match_score_gemma(
                opportunity_id=opp_id,
                scorer_version=SCORER_VERSION,
                gemma_score=score if isinstance(score, int) else 0,
                gemma_top_matches=parsed.get("top_matches", []),
                gemma_transferable=parsed.get("transferable", []),
                gemma_critical_gaps=parsed.get("critical_gaps", []),
                gemma_summary=parsed.get("summary", "") or "",
                gemma_hallucination_flags=flags,
                gemma_raw_response=raw,
            )
            scored += 1

            if args.print_each:
                print()
                print("=" * 70)
                print(
                    f"[{i}/{len(candidates)}] opp_id={opp_id} "
                    f"{opp.get('employer','?')} -- {opp.get('title','?')}"
                )
                print(
                    f"score={score}  flags={flags}  "
                    f"overlap={len(overlap_labels)}"
                )
                print(json.dumps(parsed, indent=2)[:1500])
            elif i % 10 == 0 or i == len(candidates):
                print(
                    f"  [{i}/{len(candidates)}] opp_id={opp_id} "
                    f"score={score} flags={flags}"
                )

            if args.rate_limit_sec > 0:
                time.sleep(args.rate_limit_sec)
    finally:
        tracker.close()

    print()
    print("=" * 60)
    print("GEMMA SCORING COMPLETE")
    print("=" * 60)
    print(f"Scored:           {scored}")
    print(f"Skipped (errors): {len(skipped)}")
    print(f"Total halluc flags across batch: {hallucination_total}")
    print(f"Score distribution: {dict(sorted(score_dist.items()))}")
    if skipped:
        print()
        print(f"Skipped postings ({len(skipped)}):")
        for s in skipped[:10]:
            print(
                f"  opp_id={s.get('opp_id')} "
                f"{s.get('employer','?')!r} "
                f"{s.get('title','?')!r}: {s['reason']}"
            )


if __name__ == "__main__":
    main()
