"""Diagnostic: measure inventory↔posting skill ID overlap.

Reports per taxonomy:
  - Profile_skills rows present (taxonomy names + counts)
  - Inventory unique skill IDs
  - Posting unique skill IDs
  - Intersection / overlap percentage
  - Inventory-only IDs (top 20 by label)
  - Posting-only IDs (top 20 by frequency, with labels)
  - ID-format sanity check
  - Near-miss check: do any inventory-only and posting-only IDs
    share a label string? (concept-level match, ID-level miss)

Also probes the wiring used by other code:
  - What taxonomy does scorer.py default to?
  - What taxonomy does inventory_gaps query for?
  - Does profile_skills have a row for that taxonomy?

This is a READ-ONLY script. No DB writes. Safe to run alongside
the daily pipeline.

Usage:
  python scripts/diagnose_skill_alignment.py --profile default
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import yaml

from engine.persistence.tracker import Tracker


def _list_profile_skills_taxonomies(tracker, profile_id):
    rows = tracker._query_all(
        "SELECT taxonomy, skill_ids, source_doc, extracted_at "
        "FROM profile_skills WHERE profile_id = ?",
        (profile_id,),
    )
    out = []
    for r in rows:
        try:
            ids = json.loads(r["skill_ids"]) or []
        except (TypeError, ValueError):
            ids = []
        out.append({
            "taxonomy": r["taxonomy"],
            "n_ids": len(ids),
            "n_unique": len(set(ids)),
            "source_doc": r["source_doc"],
            "extracted_at": r["extracted_at"],
            "ids": ids,
        })
    return out


def _aggregate_posting_skills(tracker, column):
    """Aggregate skills across all opportunities for the given column.

    Returns:
      counter[skill_id] -> n postings containing it
      total_postings_with_skills
    """
    rows = tracker._query_all(
        f"SELECT id, {column} FROM opportunities "
        f"WHERE {column} IS NOT NULL AND {column} != '[]'"
    )
    counter: Counter = Counter()
    n = 0
    for r in rows:
        try:
            ids = json.loads(r[column]) or []
        except (TypeError, ValueError):
            continue
        if not ids:
            continue
        n += 1
        for sid in set(ids):  # dedupe within posting
            counter[sid] += 1
    return counter, n


def _label_lookup(tracker, sids):
    if not sids:
        return {}
    return tracker.get_skill_labels(list(sids))


def _print_taxonomy_section(
    tracker, taxonomy_name, inventory_ids, posting_counter,
    posting_total,
):
    inv_set = set(inventory_ids)
    post_set = set(posting_counter.keys())
    intersection = inv_set & post_set
    inv_only = inv_set - post_set
    post_only = post_set - inv_set

    overlap_pct = (
        100.0 * len(intersection) / len(post_set)
        if post_set else 0.0
    )

    print(f"### Taxonomy: {taxonomy_name}")
    print()
    print(f"  inventory unique IDs:  {len(inv_set):>5}")
    print(f"  posting unique IDs:    {len(post_set):>5}")
    print(f"  intersection:          {len(intersection):>5}")
    print(f"  inventory-only:        {len(inv_only):>5}")
    print(f"  posting-only:          {len(post_only):>5}")
    print(
        f"  overlap as % of posting unique: {overlap_pct:.1f}%"
    )
    print()

    if inv_set:
        sample_inv = list(inv_set)[:5]
        print(f"  inventory ID samples:  {sample_inv}")
    if post_set:
        sample_post = list(post_set)[:5]
        print(f"  posting ID samples:    {sample_post}")
    print()

    # Top intersection (real matches)
    if intersection:
        print("  Top 10 INTERSECTION IDs (matches that work):")
        labels = _label_lookup(tracker, intersection)
        ranked = sorted(
            intersection,
            key=lambda s: -posting_counter.get(s, 0),
        )[:10]
        for sid in ranked:
            n = posting_counter.get(sid, 0)
            print(
                f"    {sid:<45} n_postings={n:>4} "
                f"label={labels.get(sid, '?')[:40]}"
            )
        print()

    # Top posting-only (gaps the inventory_gaps strategy would surface)
    if post_only:
        print("  Top 20 POSTING-ONLY IDs (would surface as gaps):")
        labels = _label_lookup(tracker, post_only)
        ranked = sorted(
            post_only,
            key=lambda s: -posting_counter.get(s, 0),
        )[:20]
        for sid in ranked:
            n = posting_counter.get(sid, 0)
            label = labels.get(sid, "?")
            print(
                f"    {sid:<45} n_postings={n:>4} "
                f"label={(label or '?')[:40]}"
            )
        print()

    # Top inventory-only
    if inv_only:
        print("  First 20 INVENTORY-ONLY IDs (in profile, not seen in postings):")
        labels = _label_lookup(tracker, inv_only)
        for sid in list(inv_only)[:20]:
            label = labels.get(sid, "?")
            print(f"    {sid:<45} label={(label or '?')[:40]}")
        print()

    # Near-miss check: do any inventory-only and posting-only IDs
    # share a label? Same concept, different ID.
    if inv_only and post_only:
        inv_labels = _label_lookup(tracker, inv_only)
        post_labels = _label_lookup(tracker, post_only)
        post_label_to_ids: dict[str, list[str]] = {}
        for sid, label in post_labels.items():
            if label and label != "?" and label != "<unknown>":
                post_label_to_ids.setdefault(
                    label.lower(), []
                ).append(sid)
        near_misses: list[tuple[str, str, str, list[str]]] = []
        for sid, label in inv_labels.items():
            if not label or label in ("?", "<unknown>"):
                continue
            if label.lower() in post_label_to_ids:
                near_misses.append((
                    label, sid, "->",
                    post_label_to_ids[label.lower()],
                ))
        print(
            f"  Near-misses (same label string, different IDs): "
            f"{len(near_misses)}"
        )
        for label, inv_sid, _, post_sids in near_misses[:10]:
            print(
                f"    label='{label[:35]}': inventory={inv_sid} "
                f"vs posting={post_sids[:3]}"
            )
        if near_misses:
            print()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    args = parser.parse_args()

    tracker = Tracker(args.profile)
    try:
        # --- 1. profile_skills inventory -------------------------
        print("=" * 70)
        print("PART 1 — profile_skills rows")
        print("=" * 70)
        rows = _list_profile_skills_taxonomies(tracker, args.profile)
        if not rows:
            print("(no profile_skills rows for this profile)")
        else:
            for r in rows:
                print(
                    f"  taxonomy={r['taxonomy']:<10} "
                    f"n_ids={r['n_ids']} unique={r['n_unique']} "
                    f"extracted_at={r['extracted_at']}"
                )
                print(f"    source_doc={r['source_doc']}")
        print()

        inv_by_tax = {r["taxonomy"]: r["ids"] for r in rows}

        # --- 2. posting skills aggregated across opportunities ---
        print("=" * 70)
        print("PART 2 — opportunities.extracted_skill_ids "
              "(primary, lightcast)")
        print("=" * 70)
        primary_counter, primary_total = _aggregate_posting_skills(
            tracker, "extracted_skill_ids",
        )
        print(
            f"  postings with non-empty primary skills: {primary_total}"
        )
        print(f"  unique primary skill IDs: {len(primary_counter)}")
        print()

        print("=" * 70)
        print("PART 3 — opportunities.extracted_skill_ids_secondary "
              "(esco)")
        print("=" * 70)
        secondary_counter, secondary_total = _aggregate_posting_skills(
            tracker, "extracted_skill_ids_secondary",
        )
        print(
            f"  postings with non-empty secondary skills: "
            f"{secondary_total}"
        )
        print(f"  unique secondary skill IDs: {len(secondary_counter)}")
        # Confirm the secondary_taxonomy column says "esco"
        sec_tax_row = tracker._query_one(
            "SELECT secondary_taxonomy, COUNT(*) AS n "
            "FROM opportunities "
            "WHERE extracted_skill_ids_secondary IS NOT NULL "
            "GROUP BY secondary_taxonomy"
        )
        if sec_tax_row:
            print(
                f"  secondary_taxonomy column: "
                f"{sec_tax_row['secondary_taxonomy']!r}"
            )
        print()

        # --- 4. Per-taxonomy alignment -------------------------
        print("=" * 70)
        print("PART 4 — alignment per taxonomy")
        print("=" * 70)
        # Lightcast: inventory(lightcast) vs primary postings
        lc_inv = inv_by_tax.get("lightcast", [])
        _print_taxonomy_section(
            tracker, "lightcast (PRIMARY — used by scorer)",
            lc_inv, primary_counter, primary_total,
        )
        # ESCO: inventory(esco) vs secondary postings
        esco_inv = inv_by_tax.get("esco", [])
        _print_taxonomy_section(
            tracker, "esco (secondary, annotation-only)",
            esco_inv, secondary_counter, secondary_total,
        )

        # --- 5. Wiring sanity checks ---------------------------
        print("=" * 70)
        print("PART 5 — what other code looks up")
        print("=" * 70)
        # scorer.py default: taxonomy="lightcast"
        from engine.matching.scorer import load_inventory_skill_ids
        scorer_set = load_inventory_skill_ids(
            tracker, profile_id=args.profile, taxonomy="lightcast",
        )
        print(
            f"  scorer.load_inventory_skill_ids(taxonomy='lightcast'): "
            f"{len(scorer_set)} IDs"
        )

        # inventory_gaps strategy: taxonomy comes from
        # profile YAML's `domain` field
        cfg_path = (
            PROJECT_ROOT / "config" / "profiles"
            / f"{args.profile}.yaml"
        )
        domain = "?"
        if cfg_path.exists():
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            domain = cfg.get("domain") or "(none)"
        print(
            f"  default.yaml domain field: {domain!r}"
        )
        gs_row = tracker.get_profile_skills(
            args.profile, str(domain),
        )
        gs_count = (
            len(gs_row.get("skill_ids") or []) if gs_row else 0
        )
        print(
            f"  get_profile_skills(taxonomy={domain!r}): "
            f"{'(no row)' if gs_row is None else f'{gs_count} IDs'}"
        )
        print()

        # --- 6. Verdict --------------------------------------
        print("=" * 70)
        print("PART 6 — verdict")
        print("=" * 70)
        post_set = set(primary_counter.keys())
        inv_set_lc = set(lc_inv)
        if not post_set:
            print("  No posting skills extracted yet — nothing to align.")
        else:
            overlap_pct = (
                100.0 * len(inv_set_lc & post_set) / len(post_set)
            )
            print(
                f"  Lightcast overlap as % of posting unique: "
                f"{overlap_pct:.1f}%"
            )
            if gs_row is None:
                print(
                    "  ALSO: inventory_gaps lookup taxonomy "
                    f"({domain!r}) returns no row, so it sees an "
                    "empty inventory regardless of true overlap. "
                    "This is a wiring bug — fix it BEFORE judging "
                    "the real overlap."
                )
            elif overlap_pct < 30:
                print("  Scenario: ID-level misalignment (low overlap).")
            elif overlap_pct < 60:
                print(
                    "  Scenario: partial alignment with fixable gaps."
                )
            else:
                print(
                    "  Scenario: alignment is reasonable; gaps are real."
                )
    finally:
        tracker.close()


if __name__ == "__main__":
    main()
