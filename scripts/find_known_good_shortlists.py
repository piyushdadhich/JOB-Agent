"""Find 5 known-good shortlists for Experiment 2 control set.

A 'known-good shortlist' is a posting where:
  - Human label = 'shortlist' in eval_labels
  - Current Stage 2b rated it STRONG or TOP_TIER

These are the cases the revised prompt MUST preserve (else we've
collapsed working signal in pursuit of catching the over-rated SKIPs).

Output: prints up to 10 candidates, sorted by match_score DESC, so we
can pick 5.
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
                if not line:
                    continue
                verdicts.append(json.loads(line))

        candidates = []
        for v in verdicts:
            pid = int(v["posting_id"])
            label = labels.get(pid)
            if label != "shortlist":
                continue
            tier = v.get("final_tier")
            if tier not in ("STRONG", "TOP_TIER"):
                continue
            posting = tracker.get_opportunity_by_id(pid)
            if not posting:
                continue
            candidates.append({
                "posting_id": pid,
                "match_score": int(v.get("match_score") or 0),
                "tier": tier,
                "confidence": v.get("stage2b_confidence"),
                "employer": posting.get("employer", ""),
                "title": posting.get("title", ""),
            })

        candidates.sort(key=lambda c: -c["match_score"])

        print(f"Total shortlist-labeled in verdicts: "
              f"{sum(1 for v in verdicts if labels.get(int(v['posting_id'])) == 'shortlist')}")
        print(f"Of those, currently STRONG or TOP_TIER: {len(candidates)}")
        print()
        print(f"{'pid':>5} {'score':>5} {'tier':>10} {'conf':>7} | "
              f"{'employer':<30} {'title'}")
        print("-" * 110)
        for c in candidates[:15]:
            print(
                f"{c['posting_id']:>5} {c['match_score']:>5} "
                f"{c['tier']:>10} {str(c['confidence']):>7} | "
                f"{c['employer'][:30]:<30} {c['title'][:60]}"
            )
    finally:
        tracker.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
