"""Populate config/profiles/{profile}.yaml slug lists from the
ATS detection results stored in the tracker.

Usage:
  python scripts/populate_ats_slugs.py --profile default --dry-run
  python scripts/populate_ats_slugs.py --profile default

For each ATS platform (lever, ashby, workable, personio, recruitee,
greenhouse), reads companies where ats_platform = '<platform>'
and ats_slug IS NOT NULL, then merges those slugs into the
{platform}_api.slugs list in the YAML — preserving any existing
slugs and dropping duplicates.

WARNING: writing the YAML uses yaml.safe_dump and DOES NOT preserve
comments. Run with --dry-run first to see the proposed additions —
you can paste them into the YAML manually if you want to keep
default.yaml's comments intact.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import yaml

from engine.persistence.tracker import Tracker


# Detector platform name -> profile YAML key.
_PLATFORM_TO_CONFIG_KEY = {
    "greenhouse": "greenhouse_api",
    "lever":      "lever_api",
    "ashby":      "ashby_api",
    "workable":   "workable_api",
    "personio":   "personio_api",
    "recruitee":  "recruitee_api",
}


def _slugs_for_platform(tracker, platform: str) -> list[str]:
    rows = tracker._query_all(
        "SELECT ats_slug FROM companies "
        "WHERE ats_platform = ? AND ats_slug IS NOT NULL "
        "ORDER BY ats_slug ASC",
        (platform,),
    )
    return [r["ats_slug"] for r in rows]


def _compute_changes(cfg: dict, tracker) -> dict[str, dict]:
    """Return {config_key: {existing, detected, merged, added}} for
    each platform that has new slugs to add."""
    changes: dict[str, dict] = {}
    for platform, key in _PLATFORM_TO_CONFIG_KEY.items():
        detected = _slugs_for_platform(tracker, platform)
        if not detected:
            continue
        section = cfg.get(key) or {}
        existing = list(section.get("slugs") or [])
        # dict.fromkeys preserves order while deduping. Existing first
        # so manual entries stay at the top.
        merged = list(dict.fromkeys(existing + detected))
        added = [s for s in detected if s not in existing]
        if not added:
            continue
        changes[key] = {
            "existing": existing,
            "detected": detected,
            "merged": merged,
            "added": added,
        }
    return changes


def _print_changes(changes: dict[str, dict]) -> None:
    if not changes:
        print("no changes to write — every detected slug is already "
              "in the YAML")
        return
    for key, info in changes.items():
        added = info["added"]
        merged = info["merged"]
        print(f"\n{key}:")
        print(f"  existing slugs:  {len(info['existing'])}")
        print(f"  detected slugs:  {len(info['detected'])}")
        print(f"  added slugs:     {len(added)}")
        print(f"  new total:       {len(merged)}")
        for s in added:
            print(f"    + {s}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print proposed changes without writing the YAML",
    )
    args = parser.parse_args(argv)

    yaml_path = (
        PROJECT_ROOT / "config" / "profiles" / f"{args.profile}.yaml"
    )
    if not yaml_path.exists():
        print(f"profile not found: {yaml_path}")
        return 1

    cfg = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    tracker = Tracker(args.profile)
    try:
        changes = _compute_changes(cfg, tracker)
    finally:
        tracker.close()

    _print_changes(changes)

    if not changes:
        return 0
    if args.dry_run:
        print(
            "\n(dry run — nothing written. Either re-run without "
            "--dry-run, or paste the additions into the YAML "
            "manually to preserve comments.)"
        )
        return 0

    for key, info in changes.items():
        section = cfg.setdefault(key, {})
        section["slugs"] = info["merged"]

    yaml_path.write_text(
        yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"\nwrote {yaml_path}")
    print("WARNING: comments in default.yaml were not preserved by "
          "safe_dump. Verify the file with `git diff`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
