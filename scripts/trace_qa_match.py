"""Diagnose why specific question labels are missed.

Loads patterns + profile, then runs the QAMatcher against the
exact label texts the user reports as missed. Prints the match
result for each.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.applicant.profile import load_applicant_profile
from engine.applicant.qa_matcher import QAMatcher

LABELS = [
    "Please include the full hyperlink to your LinkedIn profile here:*",
    "What are salary expectations? *",
    "What are you looking for in your next career opportunity?*",
    "Website or Marketing Portfolio",
    "How would you describe your gender identity? (mark all that apply)",
    "How would you describe your racial/ethnic background? (mark all that apply)",
    "How would you describe your sexual orientation? (mark all that apply)",
    "Do you identify as transgender? (select one)",
    "Do you have a disability or chronic condition (physical, visual, auditory, cognitive, mental, emotional, or other) that ...",
    "Are you a veteran or active member of the United States Armed Forces? (select one)",
    "Are you Hispanic/Latino?",
    "Veteran Status",
    "Disability Status",
]


def main():
    profile = load_applicant_profile("default")
    m = QAMatcher.from_yaml(profile=profile)
    print(f"Loaded {len(m.patterns)} patterns")
    print(f"Profile linkedin_url = {profile.linkedin_url!r}")
    print()
    for label in LABELS:
        result = m.match(label)
        print(f"LABEL: {label!r}")
        if result is None:
            print("  -> NO MATCH (would fall through to Tier 2)")
        else:
            print(
                f"  -> MATCH pattern_idx={result.pattern_index}  "
                f"answer={result.answer!r}  "
                f"strategy={result.strategy!r}"
            )
            print(
                f"     pattern: {m.patterns[result.pattern_index]}"
            )
        print()


if __name__ == "__main__":
    main()
