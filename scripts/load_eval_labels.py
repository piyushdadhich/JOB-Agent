"""Phase 6.0: Load eval_labels from a labeled markdown file.

Reads scripts/output/shortlist_review.md (or another file via
--file) and inserts one eval_labels row per posting based on the
checked verdict.

Re-runnable: INSERT OR REPLACE on (opportunity_id), so re-running
overwrites prior labels.

Reuses parsing logic from measure_shortlist_review.py.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.persistence.tracker import Tracker

HEADER_RE = re.compile(r"^## (\d+)\. (.+?) -- (.+)$")
OPP_ID_RE = re.compile(r"^- \*\*opp_id:\*\* `(\d+)`")
SHORTLIST_RE = re.compile(r"^- \[([ x])\] Would shortlist")
SKIP_RE = re.compile(r"^- \[([ x])\] Would skip")
UNSURE_RE = re.compile(r"^- \[([ x])\] Unsure")


def parse_labels(text: str) -> list[dict]:
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
                "verdict": None,
            }
            continue
        if cur is None:
            continue
        if (m2 := OPP_ID_RE.match(line)):
            cur["opp_id"] = int(m2.group(1))
        elif (m2 := SHORTLIST_RE.match(line)) and m2.group(1) == "x":
            cur["verdict"] = "shortlist"
        elif (m2 := SKIP_RE.match(line)) and m2.group(1) == "x":
            cur["verdict"] = "skip"
        elif (m2 := UNSURE_RE.match(line)) and m2.group(1) == "x":
            cur["verdict"] = "unsure"
    if cur is not None:
        posts.append(cur)
    return posts


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--file",
        default="scripts/output/shortlist_review.md",
        help="Markdown file to load labels from.",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"Not found: {path}")
    text = path.read_text(encoding="utf-8")
    posts = parse_labels(text)
    print(f"Parsed {len(posts)} postings from {path}")

    inserted = 0
    skipped: list[str] = []
    tracker = Tracker("default")
    try:
        for p in posts:
            if p["opp_id"] is None:
                skipped.append(
                    f"rank {p['rank']} ({p['employer']}): no opp_id"
                )
                continue
            if p["verdict"] is None:
                skipped.append(
                    f"opp_id {p['opp_id']}: no verdict checked"
                )
                continue
            tracker.insert_eval_label(
                opportunity_id=p["opp_id"],
                verdict=p["verdict"],
            )
            inserted += 1
    finally:
        tracker.close()

    print(f"Inserted: {inserted}")
    print(f"Skipped: {len(skipped)}")
    for s in skipped[:10]:
        print(f"  {s}")


if __name__ == "__main__":
    main()
