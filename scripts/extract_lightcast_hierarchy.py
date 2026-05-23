"""Extract Lightcast taxonomy hierarchy from ojd_daps_skills.

Reads venv-skills's lightcast_data_formatted.csv and writes
data/lightcast_hierarchy.json with the shape:
  {"skill_id": ["top_category", "subcategory"], ...}

Run with venv-skills (the CSV lives in that venv's site-packages).
Run once per Lightcast taxonomy version. The output JSON is checked
into the repo so the scorer (in venv\\) can load it without an ML
dependency.
"""
from __future__ import annotations

import ast
import csv
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = PROJECT_ROOT / "data" / "lightcast_hierarchy.json"


def _find_csv() -> Path:
    """Find lightcast_data_formatted.csv inside the venv-skills tree."""
    for path in PROJECT_ROOT.glob(
        "venv-skills/Lib/site-packages/ojd_daps_skills/"
        "data/lightcast_data_formatted.csv",
    ):
        return path
    raise SystemExit(
        "lightcast_data_formatted.csv not found under "
        "venv-skills/. Run from project root with venv-skills "
        "installed."
    )


def main() -> int:
    csv_path = _find_csv()
    print(f"Reading {csv_path}")

    hierarchy: dict[str, list[str]] = {}
    skipped = 0
    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sid = row.get("id")
            raw = (row.get("hierarchy_levels") or "").strip()
            if not sid or not raw:
                skipped += 1
                continue
            try:
                # Each cell is a string like "[['17.0', '17.0.442.0']]".
                # Use the first path if multiple are present.
                paths = ast.literal_eval(raw)
                if not paths or not isinstance(paths, list):
                    skipped += 1
                    continue
                first = paths[0]
                if isinstance(first, list) and first:
                    hierarchy[sid] = [str(x) for x in first]
                else:
                    skipped += 1
            except (ValueError, SyntaxError):
                skipped += 1

    print(f"Parsed {len(hierarchy)} skill IDs with hierarchy")
    print(f"Skipped (no path / unparseable): {skipped}")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(hierarchy, f, sort_keys=True)
    size_kb = OUTPUT_PATH.stat().st_size / 1024
    print(f"Wrote {OUTPUT_PATH} ({size_kb:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
