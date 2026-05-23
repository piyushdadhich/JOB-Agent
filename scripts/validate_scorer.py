"""Validate ThreeSignalScorer against the persisted posting corpus.

Reads every opportunity in tracker.db with a non-NULL
extracted_skill_ids (flat JSON list of Lightcast IDs from the
backfill_opportunity_skills.py pipeline) and scores it against
the user's Lightcast inventory from profile_skills.

Runs in venv\\ (no ML deps). The scorer itself is pure-Python
set arithmetic.

Output:
  - Console: tier distribution, top-20, bottom-20.
  - File:    scripts/output/scorer_validation.md  (full report
             with top-50, bottom-50, distribution histogram).
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.persistence.tracker import Tracker  # noqa: E402
from engine.matching.scorer import (  # noqa: E402
    ThreeSignalScorer,
    compute_idf,
    load_inventory_skill_ids,
)
from engine.matching.title_filter import (  # noqa: E402
    is_title_excluded,
    load_title_exclusions,
)
from engine.matching.bridge import (  # noqa: E402
    BridgeCalculator,
    load_lightcast_hierarchy,
)

REPORT_PATH = Path("scripts/output/scorer_validation.md")


def _safe_load_ids(blob: str | None) -> list[str]:
    if not blob:
        return []
    try:
        parsed = json.loads(blob)
    except (TypeError, ValueError):
        return []
    if isinstance(parsed, list):
        return [str(x) for x in parsed]
    if isinstance(parsed, dict) and "ids" in parsed:
        # Forward-compat for a future versioned shape; not used by
        # the current backfill but handled here for safety.
        return [str(x) for x in (parsed.get("ids") or [])]
    return []


def main() -> None:
    title_exclusions = load_title_exclusions()
    print(f"Title exclusions loaded: {len(title_exclusions)} patterns")

    hierarchy = load_lightcast_hierarchy()
    bridge_calc = BridgeCalculator(hierarchy) if hierarchy else None
    print(
        f"Lightcast hierarchy loaded: {len(hierarchy)} skill IDs"
        f" (bridge {'active' if bridge_calc else 'stubbed'})"
    )

    tracker = Tracker("default")
    try:
        inventory = load_inventory_skill_ids(
            tracker, profile_id="default", taxonomy="lightcast",
        )
        print(f"Inventory (lightcast) size: {len(inventory)} unique IDs")
        if not inventory:
            raise SystemExit("Empty inventory — cannot score.")

        idf = compute_idf(tracker)
        print(f"IDF computed over corpus: {len(idf)} skill IDs")

        all_opps = tracker.list_opportunities(limit=50_000)
        scored: list[tuple] = []  # (overall, tier, opp, result)
        excluded_by_title = 0
        scorer = ThreeSignalScorer(
            inventory, idf=idf, bridge_calculator=bridge_calc,
        )
        for opp in all_opps:
            ids = _safe_load_ids(opp.get("extracted_skill_ids"))
            if not ids:
                continue
            if is_title_excluded(opp.get("title"), title_exclusions):
                excluded_by_title += 1
                continue
            r = scorer.score(ids)
            scored.append((r.overall, r.tier, opp, r))
        print(f"Excluded by title pre-filter: {excluded_by_title}")
    finally:
        tracker.close()

    if not scored:
        raise SystemExit(
            "No opportunities with extracted_skill_ids — run backfill first."
        )

    scored.sort(key=lambda t: t[0], reverse=True)

    tier_counts = Counter(t for _, t, _, _ in scored)
    total = sum(tier_counts.values())
    print(f"\nScored opportunities: {total}")
    print("Tier distribution:")
    for tier in ("TOP_TIER", "STRONG", "EXPLORATORY", "SKIP"):
        n = tier_counts.get(tier, 0)
        pct = (n / total * 100.0) if total else 0.0
        print(f"  {tier:<12} {n:>6}  ({pct:5.1f}%)")

    print("\nTop 20 by overall:")
    for i, (overall, tier, opp, r) in enumerate(scored[:20], 1):
        print(
            f"  {i:>2}. {overall:5.2f} {tier:<12} "
            f"{(opp.get('employer') or '?')[:25]:<25} "
            f"{(opp.get('title') or '?')[:40]:<40} "
            f"matched={len(r.matched)} cov={r.coverage:.2f}"
        )

    nonzero = [t for t in scored if t[0] > 0]
    bottom_pool = (
        nonzero[-20:] if len(nonzero) >= 20 else scored[-20:]
    )
    print("\nBottom 20 (lowest non-zero) by overall:")
    for i, (overall, tier, opp, r) in enumerate(bottom_pool, 1):
        print(
            f"  {i:>2}. {overall:5.2f} {tier:<12} "
            f"{(opp.get('employer') or '?')[:25]:<25} "
            f"{(opp.get('title') or '?')[:40]:<40} "
            f"matched={len(r.matched)} cov={r.coverage:.2f}"
        )

    # --- Markdown report ---
    lines: list[str] = []
    lines.append("# Scorer validation — Lightcast + ThreeSignalScorer")
    lines.append("")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"Inventory (lightcast) size: {len(inventory)} unique IDs")
    lines.append(f"IDF support: {len(idf)} skill IDs")
    lines.append(f"Title exclusions: {len(title_exclusions)} patterns")
    lines.append(f"Excluded by title pre-filter: {excluded_by_title}")
    lines.append(
        f"Bridge signal: {'active (hierarchy ' + str(len(hierarchy)) + ' ids)' if bridge_calc else 'stub 0.5'}"
    )
    lines.append(f"Scored opportunities: {total}")
    lines.append("")
    lines.append("## Tier distribution")
    lines.append("")
    lines.append("| Tier | Count | % |")
    lines.append("|------|------:|--:|")
    for tier in ("TOP_TIER", "STRONG", "EXPLORATORY", "SKIP"):
        n = tier_counts.get(tier, 0)
        pct = (n / total * 100.0) if total else 0.0
        lines.append(f"| {tier} | {n} | {pct:.1f}% |")
    lines.append("")
    lines.append("## Top 50 by overall score")
    lines.append("")
    lines.append(
        "| Rank | Score | Tier | Cov | Rarity | Posting/Matched | "
        "Employer | Title |"
    )
    lines.append(
        "|-----:|------:|------|----:|-------:|----------------:|----------|-------|"
    )
    for i, (overall, tier, opp, r) in enumerate(scored[:50], 1):
        lines.append(
            f"| {i} | {overall:.2f} | {tier} | "
            f"{r.coverage:.2f} | {r.rarity:.2f} | "
            f"{r.posting_count}/{len(r.matched)} | "
            f"{(opp.get('employer') or '?')[:40]} | "
            f"{(opp.get('title') or '?')[:60]} |"
        )
    lines.append("")
    lines.append("## Bottom 50 by overall score (lowest first)")
    lines.append("")
    lines.append(
        "| Rank | Score | Tier | Cov | Rarity | Posting/Matched | "
        "Employer | Title |"
    )
    lines.append(
        "|-----:|------:|------|----:|-------:|----------------:|----------|-------|"
    )
    bottom = scored[-50:][::-1] if len(scored) >= 50 else scored[::-1]
    for i, (overall, tier, opp, r) in enumerate(bottom, 1):
        lines.append(
            f"| {i} | {overall:.2f} | {tier} | "
            f"{r.coverage:.2f} | {r.rarity:.2f} | "
            f"{r.posting_count}/{len(r.matched)} | "
            f"{(opp.get('employer') or '?')[:40]} | "
            f"{(opp.get('title') or '?')[:60]} |"
        )
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append(
        "- Bridge signal is stubbed at 0.5 (constant) until the "
        "Lightcast hierarchy is loaded in Phase 6; relative "
        "ranking among postings is unaffected by the constant."
    )
    lines.append(
        "- Rarity signal uses IDF computed over the in-corpus "
        "posting set with min_df=2 floor cap; specialized skills "
        "(e.g. expropriations, SAP OTC) get higher weight than "
        "ubiquitous ones."
    )
    lines.append(
        "- Coefficients (0.5/0.35/0.15) are the v4 architecture "
        "starting values. Phase 6 tunes these against the hand-"
        "labeled eval set."
    )
    lines.append(
        "- HARD STOP heuristic from Spec B2: >80% SKIP would "
        "indicate a fundamental matching problem (not just a "
        "threshold issue)."
    )

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nMarkdown report written to {REPORT_PATH}")


if __name__ == "__main__":
    main()
