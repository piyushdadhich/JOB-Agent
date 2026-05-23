"""Phase 5d Step 1: Smoke test for ojd_daps_skills first import.

First import loads spaCy model + sentence-transformers + ~500MB of
embeddings. Expected ~60 seconds on the user's hardware. Subsequent
imports are fast (~5 sec).
"""
from __future__ import annotations

import time
from typing import Any


SAMPLES = [
    # Original short sentence — keeps legacy comparison point
    ("short", "The role requires SAP implementation experience, stakeholder management across cross-functional teams, and proficiency in Agile delivery. Knowledge of payments systems is a plus."),
    # Realistic posting-style paragraph
    ("posting_like", """We are seeking a Senior Project Manager with 5+ years of experience leading enterprise software implementations. You will be responsible for managing cross-functional teams, defining project scope, creating detailed project plans, and tracking deliverables against milestones. Required skills include PMP certification, Agile and Scrum methodologies, stakeholder management, risk assessment, budget management, and proficiency with JIRA and Microsoft Project. Experience with SAP, Oracle, or similar ERP platforms is strongly preferred. The successful candidate will have excellent communication skills, the ability to manage competing priorities, and a track record of delivering projects on time and within budget."""),
]


def smoke_taxonomy(
    taxonomy_name: str,
    samples: list[tuple[str, str]],
) -> dict[str, Any]:
    """Load extractor for one taxonomy and run against each sample."""
    print(f"\n--- {taxonomy_name} ---")
    t0 = time.time()
    from ojd_daps_skills.extract_skills.extract_skills import SkillsExtractor
    sm = SkillsExtractor(taxonomy_name=taxonomy_name)
    print(f"Loaded extractor in {time.time() - t0:.1f}s")

    per_sample: list[dict[str, Any]] = []
    for label, text in samples:
        t0 = time.time()
        result = sm([text])
        elapsed = time.time() - t0

        record = result[0] if isinstance(result, list) else result
        mapped = (
            record.get("mapped_skills", [])
            if isinstance(record, dict) else []
        )
        print(
            f"  [{label}] extracted in {elapsed:.2f}s, "
            f"mapped_count={len(mapped)} (text_len={len(text)})"
        )
        for m in mapped[:5]:
            print(f"    {m}")
        per_sample.append({
            "label": label,
            "mapped_count": len(mapped),
            "elapsed_sec": elapsed,
        })

    return {
        "taxonomy": taxonomy_name,
        "per_sample": per_sample,
    }


if __name__ == "__main__":
    print("Running first-import smoke for ojd_daps_skills...")
    print("First taxonomy load will take ~60s; second is faster.")
    results = []
    for tax in ("lightcast", "esco"):
        try:
            results.append(smoke_taxonomy(tax, SAMPLES))
        except Exception as e:
            print(f"FAILED for {tax}: {type(e).__name__}: {str(e)[:500]}")
            results.append({"taxonomy": tax, "error": f"{type(e).__name__}: {str(e)[:500]}"})

    print("\n=== Summary ===")
    for r in results:
        print(r)
