"""Spec D1 TASK 5 — CLI to append a confirmed role type to the
profile's `discovered_role_types` YAML list.

Usage:
  python scripts/update_discovered_roles.py \\
      --profile default \\
      --add "Land Acquisition Officer" \\
      --confidence 0.85 \\
      [--note "from expansion report 2026-05-13"]

Behavior:
  - Loads config/profiles/{profile}.yaml
  - Appends to a `discovered_role_types` block (creating it if
    missing). Each entry is a dict {title, confidence,
    added_at, note?}.
  - Dedupes case-insensitively on title — re-adding the same
    title is a no-op (prints a notice and exits 0).
  - Writes the YAML back preserving everything else.

Never auto-runs. Always invoked by hand after the user has
reviewed scripts/output/expansion_report_{date}.md.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import yaml  # noqa: E402


def _yaml_path(profile_id: str) -> Path:
    return PROJECT_ROOT / "config" / "profiles" / f"{profile_id}.yaml"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=(
            "Append a confirmed role type to a profile's "
            "discovered_role_types list."
        ),
    )
    p.add_argument("--profile", default="default",
                   help="Profile id (default: default)")
    p.add_argument("--add", required=True,
                   help='Role title to add (e.g. "Land Acquisition Officer")')
    p.add_argument("--confidence", type=float, default=None,
                   help="Optional confidence score 0..1")
    p.add_argument("--note", default=None,
                   help="Optional free-text note (e.g. report date)")
    args = p.parse_args(argv)

    path = _yaml_path(args.profile)
    if not path.exists():
        print(f"ERROR: profile YAML not found at {path}", file=sys.stderr)
        return 2

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    discovered = data.get("discovered_role_types")
    if discovered is None:
        discovered = []
        data["discovered_role_types"] = discovered
    elif not isinstance(discovered, list):
        print(
            f"ERROR: discovered_role_types must be a list, "
            f"got {type(discovered).__name__}",
            file=sys.stderr,
        )
        return 3

    new_title = args.add.strip()
    new_norm = new_title.lower()
    for entry in discovered:
        if isinstance(entry, dict):
            existing = str(entry.get("title", "")).strip().lower()
        else:
            existing = str(entry).strip().lower()
        if existing == new_norm:
            print(
                f"'{new_title}' already in discovered_role_types — no change."
            )
            return 0

    new_entry: dict = {
        "title": new_title,
        "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if args.confidence is not None:
        new_entry["confidence"] = args.confidence
    if args.note is not None:
        new_entry["note"] = args.note

    discovered.append(new_entry)
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    print(f"Added '{new_title}' to {path.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
