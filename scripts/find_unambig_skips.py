"""Find 10 unambiguous SKIPs for the fast-eval control set.

An unambiguous SKIP is a posting where:
  - Human label = 'skip' in eval_labels
  - Current monolithic Stage 2b rated it SKIP (final_tier='SKIP'
    in eval_verdicts JSONL)

These are the cases the new pipeline MUST keep at SKIP — losing
any of them is a regression.

Picks for variety: distinct employers; mix of domains.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.persistence.tracker import Tracker

VERDICTS_PATH = (
    PROJECT_ROOT / "scripts" / "output" / "eval_verdicts_2026-05-02.jsonl"
)


def main() -> int:
    tracker = Tracker("default")
    try:
        labels_rows = tracker.list_eval_labels()
        labels = {r["opportunity_id"]: r["verdict"] for r in labels_rows}

        verdicts: list[dict] = []
        with VERDICTS_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    verdicts.append(json.loads(line))

        candidates = []
        for v in verdicts:
            pid = int(v["posting_id"])
            label = labels.get(pid)
            if label != "skip":
                continue
            tier = v.get("final_tier")
            if tier != "SKIP":
                continue
            posting = tracker.get_opportunity_by_id(pid)
            if not posting:
                continue
            candidates.append({
                "posting_id": pid,
                "match_score": int(v.get("match_score") or 0),
                "employer": posting.get("employer", ""),
                "title": posting.get("title", ""),
                "stage2a_rule": v.get("stage2a_rule_fired"),
                "stage2b_tier": v.get("stage2b_tier"),
            })

        candidates.sort(key=lambda c: (c["employer"], c["posting_id"]))

        print(f"Total unambiguous SKIPs: {len(candidates)}")
        print()
        print(f"{'pid':>5} {'rule':<22} {'2b':<10} | "
              f"{'employer':<28} {'title'}")
        print("-" * 110)
        for c in candidates:
            print(
                f"{c['posting_id']:>5} "
                f"{str(c['stage2a_rule'])[:22]:<22} "
                f"{str(c['stage2b_tier']) if c['stage2b_tier'] else '-':<10} | "
                f"{c['employer'][:28]:<28} {c['title'][:50]}"
            )
    finally:
        tracker.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
