"""Phase 5d Step 5a: score every opportunity in the tracker
with the deterministic scorer. Persist to match_scores table.

Reads:
  - profile_skills (default, lightcast) for inventory IDs
  - opportunities.extracted_skill_ids for posting IDs
  - all opportunities for IDF computation

Writes:
  - match_scores (one row per opportunity, scorer_version
    "5d-step5a-v1")

Re-runnable: INSERT OR REPLACE on (opportunity_id, scorer_version),
so re-running with a changed inventory or floor produces fresh
rows. Note: Gemma fields are nulled on re-run -- intentional,
since stale overlap means stale Gemma score.

Runs in venv\\ (no ML deps). ~1 second for 290 postings.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.matching.scorer import (
    SCORER_VERSION,
    compute_idf,
    load_inventory_skill_ids,
    score_opportunity,
)
from engine.persistence.tracker import Tracker


def main() -> None:
    print(f"Scorer version: {SCORER_VERSION}")
    tracker = Tracker("default")
    try:
        t0 = time.time()
        idf = compute_idf(tracker)
        idf_t = time.time() - t0
        print(f"IDF computed: {len(idf)} unique skill IDs "
              f"in {idf_t:.2f}s")
        if idf:
            vals = list(idf.values())
            print(f"  IDF stats: min={min(vals):.3f} "
                  f"max={max(vals):.3f} "
                  f"median={statistics.median(vals):.3f}")

        inventory = load_inventory_skill_ids(
            tracker, profile_id="default", taxonomy="lightcast",
        )
        print(f"Inventory: {len(inventory)} unique Lightcast "
              f"skill IDs")

        all_opps = tracker.list_opportunities(limit=10_000)
        eligible = [
            o for o in all_opps
            if o.get("extracted_skill_ids") is not None
        ]
        print(f"Scoring {len(eligible)} opportunities...")

        t0 = time.time()
        results = []
        for opp in eligible:
            posting_ids = json.loads(opp["extracted_skill_ids"])
            r = score_opportunity(posting_ids, inventory, idf)
            r.opportunity_id = opp["id"]
            tracker.insert_match_score(
                opportunity_id=r.opportunity_id,
                scorer_version=SCORER_VERSION,
                overlap_count=r.overlap_count,
                posting_skill_count=r.posting_skill_count,
                inventory_skill_count=r.inventory_skill_count,
                coverage_raw=r.coverage_raw,
                coverage_idf=r.coverage_idf,
                overlap_skill_ids=r.overlap_skill_ids,
                missed_skill_ids=r.missed_skill_ids,
                bucket=r.bucket,
            )
            results.append((opp, r))
        elapsed = time.time() - t0
        print(f"Scored in {elapsed:.2f}s")
    finally:
        tracker.close()

    # Distribution
    buckets = Counter(r.bucket for _, r in results)
    print("\nBucket distribution:")
    for b in ("zero", "low", "high"):
        print(f"  {b:5s}: {buckets.get(b, 0):>3}")

    coverage_idf_vals = [r.coverage_idf for _, r in results]
    if coverage_idf_vals:
        print("\ncoverage_idf stats:")
        print(f"  min:    {min(coverage_idf_vals):.3f}")
        print(f"  max:    {max(coverage_idf_vals):.3f}")
        print(f"  median: {statistics.median(coverage_idf_vals):.3f}")
        print(f"  mean:   "
              f"{sum(coverage_idf_vals) / len(coverage_idf_vals):.3f}")

    coverage_raw_vals = [r.coverage_raw for _, r in results]
    if coverage_raw_vals:
        print("\ncoverage_raw stats:")
        print(f"  min:    {min(coverage_raw_vals):.3f}")
        print(f"  max:    {max(coverage_raw_vals):.3f}")
        print(f"  median: {statistics.median(coverage_raw_vals):.3f}")
        print(f"  mean:   "
              f"{sum(coverage_raw_vals) / len(coverage_raw_vals):.3f}")

    # Top 5 by coverage_idf
    ranked = sorted(
        results, key=lambda x: x[1].coverage_idf, reverse=True,
    )
    print("\nTop 5 by coverage_idf:")
    for opp, r in ranked[:5]:
        print(
            f"  idf={r.coverage_idf:.3f}  raw={r.coverage_raw:.3f}  "
            f"overlap={r.overlap_count}/{r.posting_skill_count}  "
            f"[{r.bucket}]  "
            f"{opp.get('employer','?')[:30]:<30} "
            f"{opp.get('title','?')[:50]}"
        )

    print("\nBottom 5 non-zero by coverage_idf:")
    nonzero = [(o, r) for o, r in ranked if r.bucket != "zero"]
    for opp, r in nonzero[-5:][::-1]:
        print(
            f"  idf={r.coverage_idf:.3f}  raw={r.coverage_raw:.3f}  "
            f"overlap={r.overlap_count}/{r.posting_skill_count}  "
            f"[{r.bucket}]  "
            f"{opp.get('employer','?')[:30]:<30} "
            f"{opp.get('title','?')[:50]}"
        )


if __name__ == "__main__":
    main()
