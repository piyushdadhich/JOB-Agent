"""One-shot smoke test for LocalFormFiller against real Ollama.

Not a pytest -- hits a live Ollama instance, takes 10-30 seconds,
prints per-field latency and source (qa_matcher vs llm).

Loads PII from config/profiles/default_applicant.yaml (gitignored).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.applicant.local_filler import LocalFormFiller
from engine.applicant.profile import load_applicant_profile
from engine.applicant.qa_matcher import QAMatcher


def main() -> int:
    profile = load_applicant_profile("default")
    qa = QAMatcher.from_yaml(profile=profile)
    f = LocalFormFiller(profile, qa_matcher=qa)
    fields = [
        ("First Name", "text", []),
        ("Email", "email", []),
        ("Are you legally authorized to work in Canada?",
         "dropdown", ["Yes", "No"]),
        ("Years of project management experience", "number", []),
        ("Expected salary (CAD)", "number", []),
        ("Do you require sponsorship?", "dropdown", ["Yes", "No"]),
    ]
    total = 0.0
    for label, ftype, opts in fields:
        t0 = time.perf_counter()
        value, source = f.fill_field(
            label=label, field_type=ftype, options=opts, required=True,
        )
        dt = time.perf_counter() - t0
        total += dt
        print(f"  {dt*1000:6.0f}ms  [{source:10}]  {label}: {value!r}")
    print(f"\n  Total: {total:.2f}s for {len(fields)} fields "
          f"(avg {total/len(fields)*1000:.0f}ms/field)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
