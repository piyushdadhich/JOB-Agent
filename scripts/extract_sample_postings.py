"""Phase 5d Step 3: Run skill extraction against 10 sample postings
from tracker. Print results for human review. Does NOT persist to
opportunities -- that's Phase 5d Step 4 (backfill) after taxonomy is
chosen.

Pool to sample from: 290 total opportunities (209 JobSpy + 81
LinkedIn). LinkedIn detail-fetched postings have substantial text
(~1800-8800 chars); JobSpy varies. Sample from postings with
posting_text > 200 chars.

Also writes a markdown report to
scripts/output/sample_postings_extraction.md for human review.

RUN WITH venv-skills, NOT venv:
  .\\venv-skills\\Scripts\\python.exe scripts\\extract_sample_postings.py
"""
from __future__ import annotations

import random
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.persistence.tracker import Tracker

SAMPLE_SIZE = 10
SEED = 42  # reproducible sample selection
REPORT_PATH = Path("scripts/output/sample_postings_extraction.md")


def _format_skill_md(m: dict) -> str:
    mid = m.get("match_id") or m.get("ojo_skill_id") or "?"
    skill = m.get("match_skill", "?")
    score = m.get("match_score", 0)
    span = m.get("ojo_skill") or m.get("ojo_ner_skill") or "?"
    score_s = (
        f"{score:.2f}" if isinstance(score, (int, float)) else str(score)
    )
    return f"  - `{mid}` `{skill}` (span: `{span}`, score: {score_s})"


def main() -> None:
    random.seed(SEED)

    tracker = Tracker("default")
    try:
        all_opps = tracker.list_opportunities(limit=500)
        with_text = [
            o for o in all_opps
            if (o.get("posting_text") or "").strip()
            and len(o.get("posting_text") or "") > 200
        ]
        print(f"Total opportunities: {len(all_opps)}")
        print(f"With posting_text > 200 chars: {len(with_text)}")

        src_dist = Counter(o.get("source") for o in with_text)
        print(f"Pool by source: {dict(src_dist)}")

        if len(with_text) < SAMPLE_SIZE:
            print(
                f"WARNING: only {len(with_text)} have substantive "
                f"text; sampling all of them"
            )
            sample = with_text
        else:
            sample = random.sample(with_text, SAMPLE_SIZE)
    finally:
        tracker.close()

    from ojd_daps_skills.extract_skills.extract_skills import (
        SkillsExtractor,
    )

    extractors = {}
    for tax in ("esco", "lightcast"):
        print(f"\nLoading {tax}...")
        t0 = time.time()
        extractors[tax] = SkillsExtractor(taxonomy_name=tax)
        print(f"  loaded in {time.time() - t0:.1f}s")

    md_lines: list[str] = []
    md_lines.append("# Sample posting skill extraction -- ESCO vs Lightcast")
    md_lines.append("")
    md_lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    md_lines.append(f"Sample size: {len(sample)}  (random.seed={SEED})")
    md_lines.append(f"Pool: posting_text > 200 chars (n={len(with_text)})")
    md_lines.append("")

    for i, opp in enumerate(sample, 1):
        print(f"\n{'=' * 60}")
        print(f"Posting {i}/{len(sample)}")
        employer = opp.get("employer")
        title = opp.get("title")
        source = opp.get("source")
        print(f"  Employer: {employer}")
        print(f"  Title: {title}")
        print(f"  Source: {source}")
        text = opp.get("posting_text") or ""
        print(f"  Text len: {len(text)} chars")
        print(f"  Text preview: {text[:200]!r}")

        md_lines.append(f"## Posting {i}/{len(sample)} — {employer}")
        md_lines.append("")
        md_lines.append(f"- **Title:** {title}")
        md_lines.append(f"- **Source:** {source}")
        md_lines.append(f"- **Text length:** {len(text)} chars")
        md_lines.append("")

        per_tax_ids: dict[str, set[str]] = {}
        per_tax_counts: dict[str, int] = {}

        for tax, sm in extractors.items():
            t0 = time.time()
            doc = sm.get_skills(text)
            sm.map_skills(doc)
            elapsed = time.time() - t0
            mapped = (
                list(doc._.mapped_skills)
                if doc._.mapped_skills else []
            )
            ids_set: set[str] = set()
            for m in mapped:
                if not isinstance(m, dict):
                    continue
                sid = m.get("match_id") or m.get("ojo_skill_id")
                if not sid and isinstance(m.get("predictions"), dict):
                    sid = m["predictions"].get("match_id")
                if sid:
                    ids_set.add(str(sid))
            per_tax_ids[tax] = ids_set
            per_tax_counts[tax] = len(mapped)

            print(
                f"\n  --- {tax} ({elapsed:.2f}s, "
                f"{len(mapped)} skills) ---"
            )
            top5_by_score = sorted(
                [m for m in mapped if isinstance(m, dict)],
                key=lambda m: (
                    m.get("match_score", 0)
                    if isinstance(m.get("match_score", 0), (int, float))
                    else 0
                ),
                reverse=True,
            )[:5]
            for m in mapped[:8]:
                if not isinstance(m, dict):
                    continue
                match_id = (
                    m.get("match_id")
                    or m.get("ojo_skill_id")
                    or "?"
                )
                match_skill = m.get("match_skill", "?")
                score = m.get("match_score", 0)
                span = m.get("ojo_skill") or m.get("ojo_ner_skill") or "?"
                score_str = (
                    f"{score:.2f}"
                    if isinstance(score, (int, float)) else str(score)
                )
                print(
                    f"    {match_id} {match_skill} "
                    f"(span: '{span}', score: {score_str})"
                )

            md_lines.append(
                f"### {tax} ({elapsed:.2f}s, "
                f"{len(mapped)} skills, {len(ids_set)} unique IDs)"
            )
            md_lines.append("")
            md_lines.append("Top 5 by score:")
            for m in top5_by_score:
                md_lines.append(_format_skill_md(m))
            md_lines.append("")

        overlap = per_tax_ids["esco"] & per_tax_ids["lightcast"]
        md_lines.append(
            f"**Overlap (ESCO ∩ Lightcast unique IDs):** "
            f"{len(overlap)}  "
            f"(esco={len(per_tax_ids['esco'])}, "
            f"lightcast={len(per_tax_ids['lightcast'])})"
        )
        md_lines.append("")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(md_lines), encoding="utf-8")
    print(f"\nMarkdown report written to {REPORT_PATH}")


if __name__ == "__main__":
    main()
