"""Quick verification that ojd_daps_skills loads and extracts
non-empty mapped skills with BOTH Lightcast and ESCO taxonomies.

RUN WITH venv-skills, NOT venv:
  .\\venv-skills\\Scripts\\python.exe scripts\\verify_skills_extractor.py

Per memory project_venv_skills: real API is two-stage:
  doc = sm.get_skills(text)  (NER over bare string)
  sm.map_skills(doc)         (mutates in place)
  mapped = doc._.mapped_skills
Calling sm([text]) directly echoes input and bypasses mapping.
"""
from __future__ import annotations

import time

PROBE_TEXT = (
    "The job involves SAP implementation, stakeholder management, "
    "agile delivery, expropriations work, and SQL query tuning."
)


def verify(taxonomy: str) -> int:
    from ojd_daps_skills.extract_skills.extract_skills import (
        SkillsExtractor,
    )

    t0 = time.time()
    sm = SkillsExtractor(taxonomy_name=taxonomy)
    load_s = time.time() - t0

    t0 = time.time()
    doc = sm.get_skills(PROBE_TEXT)
    sm.map_skills(doc)
    extract_s = time.time() - t0

    mapped = list(doc._.mapped_skills) if doc._.mapped_skills else []
    print(
        f"[{taxonomy}] loaded={load_s:.1f}s extract={extract_s:.2f}s "
        f"mapped_count={len(mapped)}"
    )
    for m in mapped[:5]:
        if not isinstance(m, dict):
            continue
        mid = m.get("match_id") or m.get("ojo_skill_id") or "?"
        skill = m.get("match_skill", "?")
        score = m.get("match_score", 0)
        span = m.get("ojo_skill") or m.get("ojo_ner_skill") or "?"
        score_s = (
            f"{score:.2f}"
            if isinstance(score, (int, float)) else str(score)
        )
        print(f"  {mid} {skill} (span: {span!r}, score: {score_s})")
    return len(mapped)


def main() -> None:
    print(f"Probe text: {PROBE_TEXT!r}\n")
    lc = verify("lightcast")
    print()
    es = verify("esco")
    print()
    if lc == 0 or es == 0:
        raise SystemExit(
            f"VERIFY FAILED: lightcast={lc} esco={es}; expected >0 each"
        )
    print(f"VERIFY OK: lightcast={lc} esco={es}")


if __name__ == "__main__":
    main()
