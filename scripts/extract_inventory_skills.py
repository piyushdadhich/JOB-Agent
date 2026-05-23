"""Phase 5d Step 3: Extract skills from career_inventory.md with
both ESCO and Lightcast taxonomies. Persist to profile_skills
table. Generate a side-by-side comparison report for human review.

This script is RE-RUNNABLE. It overwrites profile_skills rows for
default.

RUN WITH venv-skills, NOT venv:
  .\\venv-skills\\Scripts\\python.exe scripts\\extract_inventory_skills.py
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.persistence.tracker import Tracker


INVENTORY_PATH = Path("source_materials/default/career_inventory.md")
REPORT_PATH = Path("scripts/output/inventory_skills_comparison.md")


def extract(
    taxonomy: str,
    text: str,
    min_score: float = 0.0,
    exclude_types: set | None = None,
) -> dict:
    """Run extractor for one taxonomy. Two-stage API.

    Stage 1: sm.get_skills(text) -- NER, returns a spaCy Doc.
    Stage 2: sm.map_skills(doc) -- mutates doc in place; mapped
             results land on doc._.mapped_skills.

    Each entry on doc._.mapped_skills is a dict with at minimum:
      ojo_ner_skill (the extracted span text), and either
      a flat match_id / match_skill / match_score set OR
      nested predictions under "predictions" (when the library
      falls back to most_common_level_N parent categories).

    Spec B2: filtering is applied here when min_score > 0 or
    exclude_types is non-empty. Lightcast production pass uses
    (0.5, {'most_common_level_1'}); ESCO stays unfiltered as the
    reference comparison line.
    """
    from ojd_daps_skills.extract_skills.extract_skills import (
        SkillsExtractor,
    )

    print(f"Loading {taxonomy} extractor...")
    t0 = time.time()
    sm = SkillsExtractor(taxonomy_name=taxonomy)
    print(f"  loaded in {time.time() - t0:.1f}s")

    print(f"Extracting from inventory ({len(text)} chars)...")
    t0 = time.time()
    doc = sm.get_skills(text)
    sm.map_skills(doc)
    elapsed = time.time() - t0
    print(f"  extracted in {elapsed:.2f}s")

    mapped_raw = list(doc._.mapped_skills) if doc._.mapped_skills else []
    excl = set(exclude_types or set())

    mapped = []
    skill_ids = []
    dropped_score = 0
    dropped_type = 0
    for m in mapped_raw:
        if not isinstance(m, dict):
            continue
        score = m.get("match_score", 0)
        if not isinstance(score, (int, float)):
            score = 0
        if score < min_score:
            dropped_score += 1
            continue
        if m.get("match_type", "") in excl:
            dropped_type += 1
            continue
        sid = m.get("match_id") or m.get("ojo_skill_id")
        if not sid and isinstance(m.get("predictions"), dict):
            sid = m["predictions"].get("match_id")
        if sid:
            skill_ids.append(str(sid))
        mapped.append(m)

    print(
        f"  filter (min_score={min_score}, exclude={sorted(excl)}): "
        f"kept {len(mapped)} of {len(mapped_raw)} "
        f"(score-dropped={dropped_score}, type-dropped={dropped_type})"
    )

    return {
        "taxonomy": taxonomy,
        "elapsed_sec": elapsed,
        "skill_count": len(mapped),
        "skill_ids": skill_ids,
        "mapped_skills": mapped,
        "raw_count": len(mapped_raw),
        "dropped_score": dropped_score,
        "dropped_type": dropped_type,
        "min_score": min_score,
        "exclude_types": sorted(excl),
    }


def write_report(esco: dict, lightcast: dict) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    esco_ids = set(esco["skill_ids"])
    lightcast_ids = set(lightcast["skill_ids"])

    lines = []
    lines.append("# Inventory skill extraction -- ESCO vs Lightcast")
    lines.append("")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"Source: `{INVENTORY_PATH}`")
    lines.append("")
    lines.append("## Summary counts")
    lines.append("")
    lines.append(f"- ESCO mapped skills: {esco['skill_count']}")
    lines.append(f"- Lightcast mapped skills: {lightcast['skill_count']}")
    lines.append(f"- ESCO unique IDs: {len(esco_ids)}")
    lines.append(f"- Lightcast unique IDs: {len(lightcast_ids)}")
    lines.append(f"- ESCO extraction time: {esco['elapsed_sec']:.1f}s")
    lines.append(
        f"- Lightcast extraction time: {lightcast['elapsed_sec']:.1f}s"
    )
    if "min_score" in lightcast:
        lines.append(
            f"- Lightcast filter: min_score={lightcast['min_score']}, "
            f"exclude_types={lightcast['exclude_types']} "
            f"(kept {lightcast['skill_count']} of {lightcast['raw_count']}; "
            f"score-dropped={lightcast['dropped_score']}, "
            f"type-dropped={lightcast['dropped_type']})"
        )
    if "min_score" in esco:
        lines.append(
            f"- ESCO filter: min_score={esco['min_score']}, "
            f"exclude_types={esco['exclude_types']} "
            f"(kept {esco['skill_count']} of {esco['raw_count']})"
        )
    lines.append("")

    lines.append("## ESCO mapped skills (sample of first 30)")
    lines.append("")
    for m in esco["mapped_skills"][:30]:
        lines.append(_format_mapped_line(m))
    lines.append("")

    lines.append("## Lightcast mapped skills (sample of first 30)")
    lines.append("")
    for m in lightcast["mapped_skills"][:30]:
        lines.append(_format_mapped_line(m))
    lines.append("")

    lines.append("## Notes for manual review")
    lines.append("")
    lines.append(
        "- Read the inventory yourself, then read each list. "
        "Which taxonomy's labels feel closer to how you'd describe "
        "the work?"
    )
    lines.append(
        "- Specifically look for: payments, SAP OTC, land services, "
        "expropriations, journals publishing, agile delivery, "
        "scrum, PMP. Which taxonomy captures these better?"
    )
    lines.append(
        "- Lightcast tends to have specific tooling (JIRA, "
        "Microsoft Project, SAP) at high confidence. ESCO tends to "
        "have crisper soft skills but falls back to parent "
        "categories on tooling. Honest read on which trade-off "
        "fits your inventory better."
    )
    lines.append(
        "- Check for false positives (e.g. 'communication' "
        "from a filler sentence)."
    )
    lines.append("")
    lines.append("## Decision")
    lines.append("")
    lines.append("- [ ] ESCO")
    lines.append("- [ ] Lightcast")

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report written to {REPORT_PATH}")


def _format_mapped_line(m: dict) -> str:
    if not isinstance(m, dict):
        return f"- (non-dict entry: {type(m).__name__})"
    # Real ojd_daps_skills 3.0.0 uses "ojo_skill" for the NER span text;
    # the spec text drafted "ojo_ner_skill". Try both for forward-compat.
    span = m.get("ojo_skill") or m.get("ojo_ner_skill") or "?"
    match_id = m.get("match_id") or m.get("ojo_skill_id") or "?"
    match_skill = m.get("match_skill", "?")
    score = m.get("match_score", 0)
    match_type = m.get("match_type", "?")
    score_str = (
        f"{score:.2f}" if isinstance(score, (int, float)) else str(score)
    )
    return (
        f"- `{match_id}` `{match_skill}` "
        f"(span: `{span}`, score: {score_str}, "
        f"type: {match_type})"
    )


def main() -> None:
    if not INVENTORY_PATH.exists():
        raise SystemExit(f"Inventory not found: {INVENTORY_PATH}")
    text = INVENTORY_PATH.read_text(encoding="utf-8")
    print(f"Loaded inventory: {len(text)} chars")

    # Spec B2-retune step 3: 0.5/0.3/0.2 all produced 99.2% SKIP;
    # threshold retune got us to 96.0% — still way over the <50%
    # goal. The type filter is the inventory cap (148 mapped/109
    # unique regardless of score). Dropping it to test whether the
    # broader inventory shifts the distribution. min_score stays
    # at 0.3 (Spec B2-retune step 1 value) since 0.2 didn't help.
    results = {}
    results["esco"] = extract("esco", text)
    results["lightcast"] = extract(
        "lightcast", text,
        min_score=0.3,
        exclude_types=set(),
    )

    tracker = Tracker("default")
    try:
        for tax, result in results.items():
            tracker.upsert_profile_skills(
                profile_id="default",
                taxonomy=tax,
                skill_ids=result["skill_ids"],
                source_doc=str(INVENTORY_PATH),
                raw_extraction=json.dumps(
                    result["mapped_skills"], default=str
                ),
            )
            # Bump v2.15 skill_count cache to len(unique skill_ids).
            unique_count = len(set(result["skill_ids"]))
            try:
                tracker._conn.execute(
                    "UPDATE profile_skills SET skill_count = ? "
                    "WHERE profile_id = ? AND taxonomy = ?",
                    (unique_count, "default", tax),
                )
                tracker._conn.commit()
            except Exception as e:
                print(
                    f"  warn: could not update skill_count for {tax}: {e}"
                )
            print(
                f"Persisted {tax}: {len(result['skill_ids'])} skill IDs "
                f"({unique_count} unique)"
            )
    finally:
        tracker.close()

    write_report(results["esco"], results["lightcast"])
    print("Done.")


if __name__ == "__main__":
    main()
