"""Validate a manually-extracted inventory_extract.json.

Companion to get_extraction_prompt.py. After the user pastes the
assembled prompt into Claude Code, copies the resulting JSON, and
saves it to data/{profile}/inventory_extract.json, this CLI:

  1. Reads the saved JSON
  2. Strips any leftover ``` fences or thinking blocks (defensive)
  3. Parses + validates against the InventoryExtract pydantic model
  4. Verifies source_hash matches the current career_inventory.md
     (warn-only; user may have intentionally extracted from an
     older inventory)
  5. Generates a diff vs the latest archived extract in
     inventory_history/ (if any)
  6. Re-writes the JSON pretty-printed in place (so it diffs cleanly
     in git)

Usage:
  python skills/inventory/validate_extract.py --profile default
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from skills.inventory.extract import (
    _strip_json_fences,
    _strip_thinking_blocks,
)
from skills.inventory.schema import InventoryExtract
from skills.inventory.staleness import compute_source_hash, write_diff


def _extract_path(profile_id: str) -> Path:
    return (
        PROJECT_ROOT / "data" / profile_id / "inventory_extract.json"
    )


def _source_path(profile_id: str) -> Path:
    return (
        PROJECT_ROOT / "source_materials" / profile_id
        / "career_inventory.md"
    )


def _history_dir(profile_id: str) -> Path:
    return PROJECT_ROOT / "data" / profile_id / "inventory_history"


def _latest_history_extract(history_dir: Path) -> Optional[Path]:
    """Return the most recent extract_*.json in history, or None."""
    if not history_dir.exists():
        return None
    candidates = sorted(history_dir.glob("extract_*.json"))
    return candidates[-1] if candidates else None


def validate(profile_id: str) -> dict:
    """Validate the saved extract for profile_id. Returns a result
    dict on success. Raises FileNotFoundError, ValueError, or
    ValidationError on failure."""
    extract_path = _extract_path(profile_id)
    source_path = _source_path(profile_id)
    history_dir = _history_dir(profile_id)

    if not extract_path.exists():
        raise FileNotFoundError(
            f"File not found at {extract_path}. Did you save the "
            f"JSON from Claude Code?"
        )

    raw = extract_path.read_text(encoding="utf-8")
    cleaned = _strip_thinking_blocks(raw)
    cleaned = _strip_json_fences(cleaned)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"JSON parse failed at line {e.lineno}, col {e.colno}: "
            f"{e.msg}\nCheck {extract_path}"
        ) from e

    extract = InventoryExtract.model_validate(parsed)

    hash_status = "MISSING_SOURCE"
    current_hash: Optional[str] = None
    if source_path.exists():
        current_hash = compute_source_hash(source_path)
        hash_status = (
            "MATCH" if current_hash == extract.source_hash
            else "MISMATCH"
        )

    extract_path.write_text(
        extract.model_dump_json(indent=2),
        encoding="utf-8",
    )

    diff_path: Optional[Path] = None
    prior = _latest_history_extract(history_dir)
    if prior is not None:
        diff_path = write_diff(prior, extract_path, history_dir)

    return {
        "extract": extract,
        "hash_status": hash_status,
        "current_source_hash": current_hash,
        "diff_path": diff_path,
        "extract_path": extract_path,
    }


def _format_validation_error(e: ValidationError) -> str:
    lines = ["Schema validation failed:"]
    for err in e.errors():
        loc = ".".join(str(p) for p in err["loc"])
        lines.append(f"  - {loc}: {err['msg']}  ({err['type']})")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    args = parser.parse_args()

    try:
        result = validate(args.profile)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except ValidationError as e:
        # Note: pydantic ValidationError subclasses ValueError, so
        # this branch must come BEFORE the ValueError catch.
        print(
            f"ERROR: {_format_validation_error(e)}",
            file=sys.stderr,
        )
        return 1
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    extract: InventoryExtract = result["extract"]
    hash_status = result["hash_status"]
    diff_path = result["diff_path"]

    if hash_status == "MISMATCH":
        print(
            "WARNING: source_hash in extract does not match the "
            "current career_inventory.md. The inventory has changed "
            "since this extract was generated.",
            file=sys.stderr,
        )
        print(
            f"  extract source_hash: {extract.source_hash[:16]}...",
            file=sys.stderr,
        )
        cur = result["current_source_hash"]
        print(
            f"  current source_hash: {cur[:16] if cur else 'n/a'}...",
            file=sys.stderr,
        )

    bar = "=" * 60
    print(bar)
    print("EXTRACT VALIDATED")
    print(bar)
    print(f"Profile:        {args.profile}")
    print(
        f"Source hash:    {extract.source_hash[:16]}... ({hash_status})"
    )
    print(f"Extracted at:   {extract.extracted_at}")
    print(f"Roles:          {len(extract.roles)}")
    print(
        f"Transferable clusters: "
        f"{len(extract.transferable_skill_clusters)}"
    )
    print(f"Hard exclusions: {len(extract.hard_exclusions)}")
    print(f"Geography:      {len(extract.geography)}")
    print(f"Diff:           {diff_path or 'no prior extract'}")
    print(bar)
    return 0


if __name__ == "__main__":
    sys.exit(main())
