"""Assemble the inventory extraction prompt for manual Claude Code use.

As of 2026-05-02, inventory extraction is performed manually by the
user via Claude Code (architecture decision #36). Local Gemma 4 E4B
extraction was abandoned after two attempts produced fabricated content
on the 51 KB input due to attention degradation under mixed CPU/GPU
offload on 4 GB VRAM.

This CLI:
  1. Reads source_materials/{profile}/career_inventory.md
  2. Reads skills/inventory/prompts/extraction_prompt.txt
  3. Substitutes placeholders ({inventory_text}, {profile_id},
     {source_hash}, {extracted_at})
  4. Writes the assembled prompt to data/{profile}/.last_extraction_prompt.txt
  5. Copies to clipboard via PowerShell Set-Clipboard
  6. Prints next-step instructions for pasting into Claude Code

Usage:
  python skills/inventory/get_extraction_prompt.py --profile default
  python skills/inventory/get_extraction_prompt.py --profile default --no-clipboard
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from skills.inventory.extract import _load_prompt, _substitute_prompt
from skills.inventory.staleness import compute_source_hash


def _source_path(profile_id: str) -> Path:
    return (
        PROJECT_ROOT / "source_materials" / profile_id
        / "career_inventory.md"
    )


def _output_path(profile_id: str) -> Path:
    return (
        PROJECT_ROOT / "data" / profile_id
        / ".last_extraction_prompt.txt"
    )


def assemble_prompt(profile_id: str) -> tuple[str, str]:
    """Return (assembled_prompt, source_hash). Raises FileNotFoundError
    if the source inventory or prompt template is missing."""
    source_path = _source_path(profile_id)
    if not source_path.exists():
        raise FileNotFoundError(
            f"Source inventory not found: {source_path}"
        )
    template = _load_prompt()
    source_hash = compute_source_hash(source_path)
    extracted_at = datetime.now(timezone.utc).isoformat()
    inventory_text = source_path.read_text(encoding="utf-8")
    prompt = _substitute_prompt(
        template,
        inventory_text=inventory_text,
        profile_id=profile_id,
        source_hash=source_hash,
        extracted_at=extracted_at,
    )
    return prompt, source_hash


def write_prompt(profile_id: str, prompt: str) -> Path:
    out_path = _output_path(profile_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(prompt, encoding="utf-8")
    return out_path


def copy_to_clipboard(text: str) -> None:
    """Copy text to the system clipboard.

    Uses the platform's native CLI tool:
      Windows  → powershell Set-Clipboard
      macOS    → pbcopy
      Linux    → xclip (preferred) or xsel

    Pipes via stdin to avoid command-line length limits. Raises
    subprocess.CalledProcessError or FileNotFoundError on failure
    so the caller can warn the user and fall back to a manual copy
    from the written-to-disk prompt file.
    """
    if sys.platform == "win32":
        cmd = ["powershell", "-NoProfile", "-Command", "Set-Clipboard"]
    elif sys.platform == "darwin":
        cmd = ["pbcopy"]
    else:
        if shutil.which("xclip"):
            cmd = ["xclip", "-selection", "clipboard"]
        elif shutil.which("xsel"):
            cmd = ["xsel", "--clipboard", "--input"]
        else:
            raise FileNotFoundError(
                "No clipboard tool found on PATH. Install `xclip` or "
                "`xsel` to enable auto-copy on Linux, or rerun with "
                "--no-clipboard and paste from the saved prompt file."
            )
    subprocess.run(
        cmd,
        input=text,
        encoding="utf-8",
        check=True,
        capture_output=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument(
        "--no-clipboard", action="store_true",
        help="Skip copying the assembled prompt to the clipboard.",
    )
    args = parser.parse_args()

    try:
        prompt, source_hash = assemble_prompt(args.profile)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    out_path = write_prompt(args.profile, prompt)

    clipboard_status = "skipped (--no-clipboard)"
    if not args.no_clipboard:
        try:
            copy_to_clipboard(prompt)
            clipboard_status = "copied"
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            clipboard_status = f"failed ({e})"
            print(
                f"WARNING: clipboard copy failed: {e}",
                file=sys.stderr,
            )

    bar = "=" * 60
    print(bar)
    print("EXTRACTION PROMPT ASSEMBLED")
    print(bar)
    print(f"Profile:        {args.profile}")
    print(f"Source hash:    {source_hash[:16]}...")
    print(f"Prompt size:    {len(prompt)} chars")
    print(f"Saved to:       {out_path}")
    print(f"Clipboard:      {clipboard_status}")
    print()
    print("NEXT STEPS:")
    print("1. Open Claude Code (or claude.ai)")
    print(
        "2. Paste the prompt (already on your clipboard, or copy "
        "from the saved file)"
    )
    print("3. Wait for Claude to produce the JSON response")
    print(
        f"4. Save the JSON to: data/{args.profile}/inventory_extract.json"
    )
    print(
        f"5. Run: python skills/inventory/validate_extract.py "
        f"--profile {args.profile}"
    )
    print(bar)
    return 0


if __name__ == "__main__":
    sys.exit(main())
