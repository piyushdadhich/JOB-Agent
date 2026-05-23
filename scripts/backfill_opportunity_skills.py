"""Phase 5d Step 4: Backfill extracted_skill_ids on every
opportunity in the tracker.

Lightcast is the PRIMARY taxonomy -- consumed by the Step 5
three-signal scorer.
ESCO is the SECONDARY taxonomy -- annotation only, displayed in
human-facing UI. Never read by scoring/matching.

Confidence floor (default 0.30) applied SYMMETRICALLY to both
taxonomies. Below-floor matches are dropped before persisting.

This script is RE-RUNNABLE. Each run overwrites both columns on
all eligible opportunities. There is no append behavior.

RUN WITH venv-skills, NOT venv:
  .\\venv-skills\\Scripts\\python.exe scripts\\backfill_opportunity_skills.py

Optional flags:
  --confidence-floor FLOAT  (default 0.50; Spec B2 raised it from 0.30)
  --min-score FLOAT         (alias for --confidence-floor)
  --exclude-type STR        (repeatable; default ['most_common_level_1'])
  --min-text-length INT     (default 200; matches Phase 5d Step 3)
  --primary {lightcast,esco}    (default lightcast)
  --secondary {esco,lightcast}  (default esco)
  --limit INT               (default None; process all)
  --dry-run                 (extract but don't write)

Spec B2 changes (2026-05-13):
  - Default confidence_floor bumped 0.30 -> 0.50 (filter low-confidence
    matches; Lightcast inventory shrank from 220 -> 109 unique).
  - --exclude-type added; default drops most_common_level_1 broad
    Lightcast category fallbacks (Biology, Manufacturing Design, etc.).
  - Both filters applied uniformly across primary and secondary
    taxonomies. The flat-JSON-list storage format is unchanged.
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

from engine.persistence.tracker import Tracker


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    # Spec B2 default 0.50; pre-B2 default was 0.30. The two flags are
    # synonyms - if both supplied, the LAST one wins (argparse default).
    p.add_argument("--confidence-floor", type=float, default=None)
    p.add_argument("--min-score", type=float, default=None)
    p.add_argument(
        "--exclude-type", action="append", default=None,
        help=(
            "match_type to exclude from kept skills. Repeatable. "
            "Default: ['most_common_level_1']."
        ),
    )
    p.add_argument("--min-text-length", type=int, default=200)
    p.add_argument(
        "--primary",
        choices=("lightcast", "esco"),
        default="lightcast",
    )
    p.add_argument(
        "--secondary",
        choices=("lightcast", "esco"),
        default="esco",
    )
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    # Resolve confidence_floor / min_score synonyms.
    if args.min_score is not None:
        args.confidence_floor = args.min_score
    if args.confidence_floor is None:
        args.confidence_floor = 0.20
    args.min_score = args.confidence_floor
    if args.exclude_type is None:
        args.exclude_type = ["most_common_level_1"]
    return args


def extract_filtered_skill_ids(
    sm, text: str, confidence_floor: float, taxonomy: str,
    exclude_types: set | list | None = None,
) -> tuple[list[str], dict, list[tuple[str, str, str, str]]]:
    """Run two-stage extraction; return (skill_ids, stats, labels).

    skill_ids is the list of taxonomy IDs after applying the
    confidence floor + type exclusion. Duplicate IDs preserved in
    order -- order reflects extraction order, not score;
    deduplication happens in the scorer at Step 5.

    stats keys: mapped_total, kept_count, dropped_count,
    fallback_count (parent-category matches that survived),
    dropped_type (skills dropped because match_type was excluded;
    counted as part of dropped_count too).

    labels is a list of (skill_id, label, taxonomy, match_type)
    tuples for every kept skill, suitable for bulk-upsert into
    skill_labels via Tracker.upsert_skill_label.
    """
    exclude = set(exclude_types or set())
    doc = sm.get_skills(text)
    sm.map_skills(doc)
    mapped = (
        list(doc._.mapped_skills) if doc._.mapped_skills else []
    )

    skill_ids: list[str] = []
    labels: list[tuple[str, str, str, str]] = []
    kept = 0
    dropped = 0
    dropped_type = 0
    fallback = 0

    for m in mapped:
        if not isinstance(m, dict):
            continue
        score = m.get("match_score", 0)
        if not isinstance(score, (int, float)):
            score = 0
        if score < confidence_floor:
            dropped += 1
            continue
        match_type = m.get("match_type", "")
        if match_type in exclude:
            dropped += 1
            dropped_type += 1
            continue
        # Prefer top-level match_id (specific match); fall back
        # to nested predictions (parent-category fallback).
        sid = m.get("match_id") or m.get("ojo_skill_id")
        if not sid and isinstance(m.get("predictions"), dict):
            sid = m["predictions"].get("match_id")
        if sid:
            sid_str = str(sid)
            skill_ids.append(sid_str)
            label = m.get("match_skill", "?")
            labels.append((sid_str, str(label), taxonomy, str(match_type)))
            kept += 1
            if "most_common_level" in match_type:
                fallback += 1
        else:
            dropped += 1

    return skill_ids, {
        "mapped_total": len(mapped),
        "kept_count": kept,
        "dropped_count": dropped,
        "dropped_type": dropped_type,
        "fallback_count": fallback,
    }, labels


def populate_inventory_labels(tracker: Tracker) -> int:
    """Read profile_skills.raw_extraction for default's lightcast
    and esco rows; upsert (skill_id, label, taxonomy, match_type)
    into skill_labels for every dict that has a usable id+label.

    Inventory labels carry source='inventory' to distinguish them
    from posting-derived labels. Same skill_id from posting backfill
    will overwrite later (last write wins) -- harmless because the
    label string is taxonomy-deterministic for a given ID.
    """
    count = 0
    for tax in ("lightcast", "esco"):
        row = tracker.get_profile_skills("default", tax)
        if not row or not row.get("raw_extraction"):
            continue
        try:
            mapped = json.loads(row["raw_extraction"])
        except (TypeError, ValueError):
            continue
        for m in mapped:
            if not isinstance(m, dict):
                continue
            sid = m.get("match_id") or m.get("ojo_skill_id")
            if not sid and isinstance(m.get("predictions"), dict):
                sid = m["predictions"].get("match_id")
            label = m.get("match_skill")
            if not sid or not label:
                continue
            mtype = m.get("match_type", "?")
            tracker.upsert_skill_label(
                str(sid), str(label), tax, str(mtype),
                source="inventory",
            )
            count += 1
    return count


def main() -> None:
    args = parse_args()
    exclude_types_set = set(args.exclude_type)
    print(
        f"Backfill: primary={args.primary}, secondary={args.secondary}, "
        f"floor={args.confidence_floor}, "
        f"exclude_types={sorted(exclude_types_set)}, "
        f"min_text={args.min_text_length}, "
        f"limit={args.limit}, dry_run={args.dry_run}"
    )
    if args.primary == args.secondary:
        raise SystemExit(
            "primary and secondary must be different taxonomies"
        )

    tracker = Tracker("default")
    try:
        all_opps = tracker.list_opportunities(limit=10_000)
        eligible = [
            o for o in all_opps
            if (o.get("posting_text") or "").strip()
            and len(o.get("posting_text") or "")
            >= args.min_text_length
        ]
        print(f"Total opportunities: {len(all_opps)}")
        print(
            f"Eligible (text >= {args.min_text_length} chars): "
            f"{len(eligible)}"
        )
        if args.limit is not None:
            eligible = eligible[: args.limit]
            print(f"Limited to first {len(eligible)}")
    finally:
        tracker.close()

    from ojd_daps_skills.extract_skills.extract_skills import (
        SkillsExtractor,
    )

    print(f"\nLoading {args.primary} extractor...")
    t0 = time.time()
    primary_sm = SkillsExtractor(taxonomy_name=args.primary)
    print(f"  loaded in {time.time() - t0:.1f}s")

    print(f"Loading {args.secondary} extractor...")
    t0 = time.time()
    secondary_sm = SkillsExtractor(taxonomy_name=args.secondary)
    print(f"  loaded in {time.time() - t0:.1f}s")

    success = 0
    skipped: list[dict] = []
    primary_zero = 0
    secondary_zero = 0
    primary_kept_total = 0
    secondary_kept_total = 0
    primary_dropped_total = 0
    secondary_dropped_total = 0

    tracker = Tracker("default")
    try:
        if not args.dry_run:
            inv_count = populate_inventory_labels(tracker)
            print(f"Loaded {inv_count} inventory skill labels")

        for i, opp in enumerate(eligible, 1):
            opp_id = opp["id"]
            text = opp.get("posting_text") or ""
            employer = opp.get("employer", "?")
            title = opp.get("title", "?")

            try:
                primary_ids, primary_stats, primary_labels = (
                    extract_filtered_skill_ids(
                        primary_sm, text, args.confidence_floor,
                        args.primary, exclude_types=exclude_types_set,
                    )
                )
                secondary_ids, secondary_stats, secondary_labels = (
                    extract_filtered_skill_ids(
                        secondary_sm, text, args.confidence_floor,
                        args.secondary, exclude_types=exclude_types_set,
                    )
                )
            except Exception as e:
                skipped.append({
                    "opp_id": opp_id,
                    "employer": employer,
                    "title": title,
                    "reason": f"{type(e).__name__}: {str(e)[:200]}",
                })
                continue

            primary_kept_total += primary_stats["kept_count"]
            secondary_kept_total += secondary_stats["kept_count"]
            primary_dropped_total += primary_stats["dropped_count"]
            secondary_dropped_total += secondary_stats["dropped_count"]
            if primary_stats["kept_count"] == 0:
                primary_zero += 1
            if secondary_stats["kept_count"] == 0:
                secondary_zero += 1

            if not args.dry_run:
                tracker.update_opportunity_skills_dual(
                    opportunity_id=opp_id,
                    primary_skill_ids=primary_ids,
                    secondary_skill_ids=secondary_ids,
                    secondary_taxonomy=args.secondary,
                )
                for sid, label, tax, mtype in (
                    primary_labels + secondary_labels
                ):
                    tracker.upsert_skill_label(
                        sid, label, tax, mtype,
                    )

            if i % 25 == 0 or i == len(eligible):
                print(
                    f"  [{i}/{len(eligible)}] {employer[:30]:<30} "
                    f"{title[:35]:<35} "
                    f"{args.primary}={primary_stats['kept_count']:>3} "
                    f"{args.secondary}={secondary_stats['kept_count']:>3}"
                )
            success += 1
    finally:
        tracker.close()

    print()
    print("=" * 60)
    print("BACKFILL COMPLETE")
    print("=" * 60)
    print(f"Eligible:           {len(eligible)}")
    print(f"Success:            {success}")
    print(f"Skipped (errors):   {len(skipped)}")
    print(
        f"Posts with 0 {args.primary} matches above floor:    "
        f"{primary_zero}"
    )
    print(
        f"Posts with 0 {args.secondary} matches above floor:  "
        f"{secondary_zero}"
    )
    print(
        f"Total {args.primary} skill IDs kept:    "
        f"{primary_kept_total}"
    )
    print(
        f"Total {args.primary} skill IDs dropped: "
        f"{primary_dropped_total}"
    )
    print(
        f"Total {args.secondary} skill IDs kept:    "
        f"{secondary_kept_total}"
    )
    print(
        f"Total {args.secondary} skill IDs dropped: "
        f"{secondary_dropped_total}"
    )

    if skipped:
        print()
        print(f"Skipped postings ({len(skipped)}):")
        for s in skipped[:10]:
            print(
                f"  opp_id={s['opp_id']} {s['employer']!r} "
                f"{s['title']!r}: {s['reason']}"
            )
        if len(skipped) > 10:
            print(f"  ... and {len(skipped) - 10} more")

    if args.dry_run:
        print()
        print("DRY RUN -- no rows written.")


if __name__ == "__main__":
    main()
