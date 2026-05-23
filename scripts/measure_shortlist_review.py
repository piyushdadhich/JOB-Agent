"""Phase 5d Step 5c: Measure precision/agreement from labeled
shortlist.

Reads scripts/output/shortlist_review.md after Alex has
marked the checkboxes. For each posting in the file, finds
which checkbox was checked (`[x]`) and reports:

  - Precision: fraction of top-30 marked 'Would shortlist'
  - False positives: high-scored postings marked 'Would skip'
  - Agreement: did the top-half (higher-ranked) get a higher
    shortlist rate than the bottom-half?
  - Per-Gemma-score breakdown
  - Missing: any opp_ids Alex flagged in the '## Missing'
    section

Diagnostic only -- no DB writes.

Runs in venv\\ (no ML deps).
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

INPUT_PATH = Path("scripts/output/shortlist_review.md")

HEADER_RE = re.compile(r"^## (\d+)\. (.+?) -- (.+)$")
OPP_ID_RE = re.compile(r"^- \*\*opp_id:\*\* `(\d+)`")
GEMMA_SCORE_RE = re.compile(
    r"\*\*Gemma score: `(\d+)`\*\*"
)
COVERAGE_IDF_RE = re.compile(
    r"^- coverage_idf: `([\d.]+)`"
)
SHORTLIST_RE = re.compile(r"^- \[([ x])\] Would shortlist")
SKIP_RE = re.compile(r"^- \[([ x])\] Would skip")
UNSURE_RE = re.compile(r"^- \[([ x])\] Unsure")


def parse_shortlist(text: str) -> list[dict]:
    """Walk through markdown, collect one record per posting."""
    posts: list[dict] = []
    cur: dict | None = None
    for line in text.splitlines():
        m = HEADER_RE.match(line)
        if m:
            if cur is not None:
                posts.append(cur)
            cur = {
                "rank": int(m.group(1)),
                "employer": m.group(2).strip(),
                "title": m.group(3).strip(),
                "opp_id": None,
                "gemma_score": None,
                "coverage_idf": None,
                "verdict": None,
            }
            continue
        if cur is None:
            continue
        if (m2 := OPP_ID_RE.match(line)):
            cur["opp_id"] = int(m2.group(1))
        elif (m2 := COVERAGE_IDF_RE.match(line)):
            cur["coverage_idf"] = float(m2.group(1))
        elif (m2 := GEMMA_SCORE_RE.search(line)):
            cur["gemma_score"] = int(m2.group(1))
        elif (m2 := SHORTLIST_RE.match(line)) and m2.group(1) == "x":
            cur["verdict"] = "shortlist"
        elif (m2 := SKIP_RE.match(line)) and m2.group(1) == "x":
            cur["verdict"] = "skip"
        elif (m2 := UNSURE_RE.match(line)) and m2.group(1) == "x":
            cur["verdict"] = "unsure"
    if cur is not None:
        posts.append(cur)
    return posts


def parse_missing_section(text: str) -> list[str]:
    """Lines under '## Missing' that look like 'NN: ...'."""
    in_missing = False
    out: list[str] = []
    for line in text.splitlines():
        if line.strip() == "## Missing":
            in_missing = True
            continue
        if not in_missing:
            continue
        if line.startswith("##"):
            break
        stripped = line.strip()
        if (stripped and not stripped.startswith("_")
                and not stripped.startswith("#")):
            out.append(stripped)
    return out


def main() -> None:
    if not INPUT_PATH.exists():
        raise SystemExit(f"Not found: {INPUT_PATH}")
    text = INPUT_PATH.read_text(encoding="utf-8")
    posts = parse_shortlist(text)
    missing = parse_missing_section(text)

    print(f"Read {len(posts)} postings from {INPUT_PATH}")

    verdict_dist = Counter(p["verdict"] for p in posts)
    print(f"\nVerdict distribution: {dict(verdict_dist)}")

    unlabeled = [p for p in posts if p["verdict"] is None]
    if unlabeled:
        print(f"WARNING: {len(unlabeled)} postings unlabeled. "
              f"Numbers below treat them as not-shortlisted.")

    n = len(posts)
    n_shortlist = sum(
        1 for p in posts if p["verdict"] == "shortlist"
    )
    n_skip = sum(1 for p in posts if p["verdict"] == "skip")
    n_unsure = sum(1 for p in posts if p["verdict"] == "unsure")

    if n > 0:
        precision = n_shortlist / n
        print(f"\nPrecision (shortlist / total): "
              f"{n_shortlist}/{n} = {precision:.1%}")
        print(f"False positives (skip / total): "
              f"{n_skip}/{n} = {n_skip/n:.1%}")
        print(f"Unsure: {n_unsure}/{n} = {n_unsure/n:.1%}")

    # Agreement by half.
    halfway = n // 2
    top_half = posts[:halfway]
    bottom_half = posts[halfway:]
    top_short = sum(
        1 for p in top_half if p["verdict"] == "shortlist"
    )
    bot_short = sum(
        1 for p in bottom_half if p["verdict"] == "shortlist"
    )
    print(f"\nTop-half ({len(top_half)}) shortlist rate: "
          f"{top_short}/{len(top_half)} = "
          f"{top_short/max(len(top_half),1):.1%}")
    print(f"Bottom-half ({len(bottom_half)}) shortlist rate: "
          f"{bot_short}/{len(bottom_half)} = "
          f"{bot_short/max(len(bottom_half),1):.1%}")

    # Per-Gemma-score breakdown.
    gemma_buckets: dict[int, list[str | None]] = {}
    for p in posts:
        if p["gemma_score"] is None:
            continue
        gemma_buckets.setdefault(
            p["gemma_score"], []
        ).append(p["verdict"])
    if gemma_buckets:
        print("\nVerdict by Gemma score:")
        for score in sorted(gemma_buckets, reverse=True):
            verdicts = gemma_buckets[score]
            short = sum(1 for v in verdicts if v == "shortlist")
            skip = sum(1 for v in verdicts if v == "skip")
            unsure = sum(1 for v in verdicts if v == "unsure")
            print(
                f"  gemma={score}  n={len(verdicts):>2}  "
                f"shortlist={short}  skip={skip}  unsure={unsure}"
            )

    if missing:
        print(f"\nMissing postings flagged ({len(missing)}):")
        for m in missing:
            print(f"  {m}")


if __name__ == "__main__":
    main()
