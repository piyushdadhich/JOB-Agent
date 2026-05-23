"""Regenerate inventory_extract.json from career_inventory.md.

Backends:
  --backend gemma4-e4b-local  (default; uses Ollama at localhost)
  --backend gemma4-31b-api    (stub; uses Google AI Studio when wired)
  --backend claude            (stub; uses Claude API when wired)

Usage:
  python skills/inventory/extract.py --profile default
  python skills/inventory/extract.py --profile default --force
  python skills/inventory/extract.py --profile default --backend claude
  python skills/inventory/extract.py --profile default --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import requests
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from skills.inventory.schema import InventoryExtract
from skills.inventory.staleness import (
    archive_current_extract,
    compute_source_hash,
    is_stale,
    write_diff,
)

PROMPT_PATH = (
    Path(__file__).resolve().parent / "prompts"
    / "extraction_prompt.txt"
)


# --- Backends --------------------------------------------------------

class ExtractionBackend(Protocol):
    name: str
    def extract(self, prompt: str) -> str: ...


class GemmaLocalBackend:
    """Local Gemma 4 E4B backend for inventory extraction.

    DEPRECATED for inventory extraction. As of 2026-05-02, inventory
    extraction is performed manually by the user via Claude Code. See
    architecture decision #36. Two extraction attempts via this backend
    produced fabricated content due to attention degradation on 51 KB
    input running with mixed CPU/GPU offload on 4 GB VRAM. Failed
    runs preserved at:
      data/default/inventory_history/failed_*.txt

    Use skills/inventory/get_extraction_prompt.py + Claude Code instead.

    Class retained for: (a) future hardware upgrades, (b) scaffolding
    reference, (c) potential per-role chunked extraction if manual
    Claude Code extraction becomes too burdensome.
    """
    name = "gemma4-e4b-local"

    def __init__(
        self,
        model: str = "gemma4:e4b",
        url: str = "http://localhost:11434/api/generate",
        num_predict: int = 8000,
        temperature: float = 0.1,
        timeout: int = 3600,
    ):
        self.model = model
        self.url = url
        self.num_predict = num_predict
        self.temperature = temperature
        self.timeout = timeout

    def extract(self, prompt: str) -> str:
        resp = requests.post(
            self.url,
            json={
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": self.temperature,
                    "num_predict": self.num_predict,
                },
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json().get("response", "")


class GemmaAPIBackend:
    name = "gemma4-31b-api"

    def extract(self, prompt: str) -> str:
        raise NotImplementedError(
            "API backend not yet implemented; see Step 4b. "
            "Will require GEMINI_API_KEY env var."
        )


class ClaudeBackend:
    name = "claude"

    def extract(self, prompt: str) -> str:
        raise NotImplementedError(
            "Claude backend not yet implemented; see Step 4b. "
            "Will require ANTHROPIC_API_KEY env var."
        )


_BACKENDS: dict[str, type] = {
    "gemma4-e4b-local": GemmaLocalBackend,
    "gemma4-31b-api": GemmaAPIBackend,
    "claude": ClaudeBackend,
}


def get_backend(name: str) -> ExtractionBackend:
    if name not in _BACKENDS:
        raise ValueError(
            f"Unknown backend: {name}. "
            f"Choices: {sorted(_BACKENDS)}"
        )
    return _BACKENDS[name]()


# --- Helpers ---------------------------------------------------------

_THINK_TAG_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
_THINKING_MARKER_RE = re.compile(
    r"Thinking\.\.\..*?\.\.\.done thinking\.\s*", re.DOTALL,
)
_FENCE_RE = re.compile(
    r"```(?:json)?\s*\n?(.*)\n?```", re.DOTALL,
)


def _strip_thinking_blocks(text: str) -> str:
    """Remove Gemma 4 thinking-mode preambles before JSON.

    Patterns observed:
      - 'Thinking...\\n...content...\\n...done thinking.\\n'
      - '<think>...</think>'

    If no thinking marker is present, returns the input with
    only outer whitespace stripped.
    """
    text = _THINK_TAG_RE.sub("", text)
    text = _THINKING_MARKER_RE.sub("", text)
    return text.strip()


def _strip_json_fences(text: str) -> str:
    """Return content between first and last ``` markers; or the
    full input (stripped) if no fences are present."""
    text = text.strip()
    m = _FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    return text


def _load_prompt(template_path: Path = PROMPT_PATH) -> str:
    return template_path.read_text(encoding="utf-8")


_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")
_SCHEMA_MARKER = "OUTPUT JSON SCHEMA"


def _substitute_prompt(template: str, **vars) -> str:
    """Replace {var} placeholders. Uses str.replace, not
    str.format, because the OUTPUT JSON SCHEMA section in the
    template has literal `{` and `}` braces that would otherwise
    require escaping.

    After substitution, scans the pre-schema portion of the
    result for any surviving {token} patterns and raises if
    any remain -- guards against typos in vars or new template
    placeholders the caller forgot to wire up.
    """
    result = template
    for k, v in vars.items():
        result = result.replace("{" + k + "}", str(v))

    pre_schema = result.split(_SCHEMA_MARKER, 1)[0]
    leftover = _PLACEHOLDER_RE.findall(pre_schema)
    if leftover:
        raise ValueError(
            f"Unsubstituted tokens remain in prompt: "
            f"{sorted(set(leftover))}"
        )
    return result


def _build_prompt_for(profile_id: str) -> tuple[str, Path, str]:
    """Assemble the prompt for a profile. Returns
    (prompt, source_path, source_hash)."""
    source_path = (
        PROJECT_ROOT / "source_materials" / profile_id
        / "career_inventory.md"
    )
    if not source_path.exists():
        raise FileNotFoundError(f"Source not found: {source_path}")
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
    return prompt, source_path, source_hash


# --- Core regeneration ----------------------------------------------

def _save_failed_response(
    raw: str, history_dir: Path, backend_name: str,
) -> Path:
    history_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    p = history_dir / f"failed_{ts}_{backend_name}.txt"
    p.write_text(raw, encoding="utf-8")
    return p


def regenerate(
    profile_id: str,
    *,
    force: bool = False,
    backend_name: str = "gemma4-e4b-local",
) -> dict:
    """Read source, optionally regen, write extract + diff.

    On success returns a dict describing the action. On failure
    raises with a message indicating where the raw failed
    response was saved (so the caller can inspect after the fact).
    """
    extract_path = (
        PROJECT_ROOT / "data" / profile_id / "inventory_extract.json"
    )
    history_dir = (
        PROJECT_ROOT / "data" / profile_id / "inventory_history"
    )
    source_path = (
        PROJECT_ROOT / "source_materials" / profile_id
        / "career_inventory.md"
    )
    if not source_path.exists():
        raise FileNotFoundError(f"Source not found: {source_path}")

    if not force:
        stale, reason = is_stale(source_path, extract_path)
        if not stale:
            return {
                "action": "skipped",
                "reason": reason,
                "backend_used": None,
            }

    prompt, _, source_hash = _build_prompt_for(profile_id)

    archived = None
    if extract_path.exists():
        archived = archive_current_extract(extract_path, history_dir)

    backend = get_backend(backend_name)
    start = time.time()
    try:
        raw = backend.extract(prompt)
    except requests.exceptions.ConnectionError as e:
        raise ConnectionError(
            "Could not reach Ollama at localhost:11434. "
            "Is it running? Try: ollama serve"
        ) from e
    except requests.exceptions.Timeout as e:
        raise TimeoutError(
            "Ollama did not return within timeout. Backend was "
            "actively processing -- increase timeout or check if "
            "model is too slow on this hardware."
        ) from e
    elapsed = time.time() - start

    cleaned = _strip_thinking_blocks(raw)
    cleaned = _strip_json_fences(cleaned)

    if not cleaned.strip():
        raise ValueError(
            "Ollama returned empty response. Model may have "
            "crashed. Check: ollama ps"
        )

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as e:
        failed = _save_failed_response(raw, history_dir, backend.name)
        raise ValueError(
            f"JSON parse failed ({e}); "
            f"raw response saved to {failed}"
        ) from e

    try:
        extract = InventoryExtract.model_validate(parsed)
    except ValidationError as e:
        failed = _save_failed_response(raw, history_dir, backend.name)
        raise ValueError(
            f"Schema validation failed; "
            f"raw response saved to {failed}\n{e}"
        ) from e

    extract_path.parent.mkdir(parents=True, exist_ok=True)
    extract_path.write_text(
        extract.model_dump_json(indent=2),
        encoding="utf-8",
    )

    diff_path = None
    if archived:
        diff_path = write_diff(archived, extract_path, history_dir)

    return {
        "action": "regenerated",
        "backend_used": backend.name,
        "elapsed_sec": round(elapsed, 1),
        "extract_path": str(extract_path),
        "archived": str(archived) if archived else None,
        "diff_path": str(diff_path) if diff_path else None,
        "roles_count": len(extract.roles),
        "transferable_clusters_count": len(
            extract.transferable_skill_clusters,
        ),
        "source_hash": source_hash,
    }


# --- CLI -------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--backend",
        default="gemma4-e4b-local",
        choices=sorted(_BACKENDS),
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Build prompt and print preview; no LLM call.",
    )
    args = parser.parse_args()

    if args.dry_run:
        try:
            prompt, source_path, source_hash = _build_prompt_for(
                args.profile,
            )
        except FileNotFoundError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        print("=== DRY RUN ===")
        print(f"Profile:        {args.profile}")
        print(f"Backend:        {args.backend} (would be used)")
        print(f"Source:         {source_path}")
        print(f"Source hash:    {source_hash[:16]}...")
        print(f"Prompt length:  {len(prompt)} chars")
        print("--- First 500 chars of assembled prompt: ---")
        print(prompt[:500])
        print("--- Last 200 chars: ---")
        print(prompt[-200:])
        return 0

    try:
        result = regenerate(
            args.profile,
            force=args.force,
            backend_name=args.backend,
        )
        print(json.dumps(result, indent=2, default=str))
        return 0
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
