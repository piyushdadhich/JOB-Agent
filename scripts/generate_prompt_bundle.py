"""CLI wrapper around agents.prompt_bundles.PromptBundleGenerator.

Usage:
    python scripts\\generate_prompt_bundle.py --opportunity-id 42
    python scripts\\generate_prompt_bundle.py --opportunity-id 42 --type cover_letter
    python scripts\\generate_prompt_bundle.py --opportunity-id 42 --resume-variant financial_services
    python scripts\\generate_prompt_bundle.py --opportunity-id 42 --type recruiter_followup --purpose status_check
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.prompt_bundles import (  # noqa: E402
    PromptBundleGenerator, PromptBundleError, BUNDLE_TYPES,
)
from engine.persistence.tracker import RESUME_VARIANTS  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Generate a Claude.ai Max prompt bundle for an opportunity."
    )
    p.add_argument("--opportunity-id", type=int, required=True)
    p.add_argument("--type", dest="bundle_type", choices=sorted(BUNDLE_TYPES),
                   default=None)
    p.add_argument("--resume-variant", choices=sorted(RESUME_VARIANTS),
                   default=None)
    p.add_argument("--purpose", default=None,
                   help="Purpose string for recruiter_followup bundles.")
    args = p.parse_args(argv)

    gen = PromptBundleGenerator()
    try:
        result = gen.generate_bundle(
            opportunity_id=args.opportunity_id,
            bundle_type=args.bundle_type,
            resume_variant_override=args.resume_variant,
            purpose=args.purpose,
        )
    except PromptBundleError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    print(f"Bundle generated at {result['bundle_path']}.")
    print(f"  application_id : {result['application_id']}")
    print(f"  bundle_type    : {result['bundle_type']}")
    print(f"  resume_variant : {result['resume_variant']}")
    print(f"  folder         : {result['folder_path']}")
    print()
    print("Open it, paste into Claude.ai Max, then run save_draft.py "
          "when ready to save the output.")
    print(f"Reference application id {result['application_id']} "
          f"when calling save_draft.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
