"""Render an inventory_summary.md from inventory_extract.json.

Stage 2b LLM evaluation needs a compact (~5 KB) markdown digest of the
~14 KB inventory extract that fits in a Gemma 4 E4B context window
alongside a job posting and the evaluation prompt. This CLI:

  1. Reads data/{profile}/inventory_extract.json
  2. Validates it against the InventoryExtract pydantic model
  3. Renders skills/inventory/templates/summary.md.j2
  4. Writes data/{profile}/inventory_summary.md
  5. Prints byte / role / cluster counts

Usage:
  python skills/inventory/render_summary.py --profile default
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, TemplateError
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from skills.inventory.schema import InventoryExtract

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
TEMPLATE_NAME = "summary.md.j2"


def _extract_path(profile_id: str) -> Path:
    return (
        PROJECT_ROOT / "data" / profile_id / "inventory_extract.json"
    )


def _summary_path(profile_id: str) -> Path:
    return (
        PROJECT_ROOT / "data" / profile_id / "inventory_summary.md"
    )


def _build_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render(profile_id: str) -> dict:
    """Render the inventory summary for profile_id.

    Returns a metadata dict: summary_path, byte_count, role_count,
    cluster_count.

    Raises FileNotFoundError if the extract is missing, ValueError on
    JSON parse failure, ValidationError on schema violations, and
    jinja2.TemplateError on template issues.
    """
    extract_path = _extract_path(profile_id)
    if not extract_path.exists():
        raise FileNotFoundError(
            f"Inventory extract not found at {extract_path}. Run "
            f"get_extraction_prompt.py first, paste the prompt into "
            f"Claude Code, save the JSON, then run validate_extract.py."
        )

    raw = extract_path.read_text(encoding="utf-8")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"JSON parse failed at line {e.lineno}, col {e.colno}: "
            f"{e.msg}"
        ) from e

    extract = InventoryExtract.model_validate(parsed)

    env = _build_env()
    template = env.get_template(TEMPLATE_NAME)
    rendered = template.render(**extract.model_dump())

    summary_path = _summary_path(profile_id)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(rendered, encoding="utf-8")

    return {
        "summary_path": summary_path,
        "byte_count": len(rendered.encode("utf-8")),
        "role_count": len(extract.roles),
        "cluster_count": len(extract.transferable_skill_clusters),
    }


def _format_validation_error(e: ValidationError) -> str:
    lines = ["Schema validation failed:"]
    for err in e.errors()[:3]:
        loc = ".".join(str(p) for p in err["loc"])
        lines.append(f"  - {loc}: {err['msg']}  ({err['type']})")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    args = parser.parse_args()

    try:
        result = render(args.profile)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except ValidationError as e:
        print(
            f"ERROR: {_format_validation_error(e)}",
            file=sys.stderr,
        )
        return 1
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except TemplateError as e:
        print(f"ERROR: template render failed: {e}", file=sys.stderr)
        return 1

    rel = result["summary_path"].relative_to(PROJECT_ROOT)
    print(
        f"Wrote {rel} ({result['byte_count']:,} bytes; "
        f"{result['role_count']} roles, "
        f"{result['cluster_count']} clusters)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
