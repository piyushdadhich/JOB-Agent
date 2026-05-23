"""Stage 2pre: Gemma 3 4B PROCEED/SKIP filter.

The first LLM stage of the v2.3 evaluator pipeline. Cheap binary
classification deciding whether a posting is worth deeper evaluation.

Bias is toward PROCEED:
  - False PROCEED costs ~2-3 minutes of decide-stage evaluation.
  - False SKIP loses an opportunity permanently.

When the LLM raises, returns nonsense, or emits an invalid verdict,
we fail-open (default to PROCEED). The decide stage will sort out
genuine matches from PROCEED noise; better that than dropping a
real match because the filter model misbehaved.

Uses generate_chat() with system+user message separation. Note:
on Ollama 0.22.1 (2026-05-03) prefix caching does not fire for
the gemma3 architecture even with this separation; the structure
is kept anyway because (a) the architecture is correct and (b)
future Ollama versions may close that gap.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

logger = logging.getLogger(__name__)

VALID_SKIP_REASONS = (
    "NO_FUNCTIONAL_OVERLAP",
    "WRONG_SENIORITY",
    "GEO_MISMATCH",
    "OTHER",
)

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_PROMPT_NAME = "stage2pre_prompt.txt"
_DEFAULT_MODEL = "gemma3-4b-ctx4k"
_NUM_PREDICT = 200
_NUM_CTX = 4096
_TEMPERATURE = 0.0
_KEEP_ALIVE = -1
_POSTING_DESCRIPTION_TRUNCATE = 4000

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_VERDICT_FIELD_RE = re.compile(
    r'"verdict"\s*:\s*"(PROCEED|SKIP)"', re.IGNORECASE
)


def _try_template_echo(raw: str) -> Optional[dict]:
    """Detect Gemma template-echo and synthesize a verdict.

    Older versions of the prompt exposed a pipe-separated schema like
    `null | "NO_FUNCTIONAL_OVERLAP" | "WRONG_SENIORITY" | ...`. Gemma
    occasionally copies that schema verbatim instead of choosing one
    branch, producing strings like:
      {"verdict": "PROCEED", "skip_reason": null | "NO_FUNCTIONAL_..."}
    which json.loads cannot parse.

    The current prompt uses concrete examples to prevent this entirely
    (see stage2pre_prompt.txt). This is defense-in-depth so a future
    prompt-template regression doesn't silently fail-open every call.
    Returns None when `raw` doesn't look like a template echo.
    """
    if "null |" not in raw and '| "' not in raw:
        return None
    m = _VERDICT_FIELD_RE.search(raw)
    if m:
        return {"verdict": m.group(1).upper(), "skip_reason": None}
    upper = raw.upper()
    p_idx = upper.find("PROCEED")
    s_idx = upper.find("SKIP")
    if p_idx == -1 and s_idx == -1:
        return None
    if s_idx == -1 or (p_idx != -1 and p_idx < s_idx):
        return {"verdict": "PROCEED", "skip_reason": None}
    return {"verdict": "SKIP", "skip_reason": None}


@dataclass(frozen=True)
class Stage2PreResult:
    verdict: Literal["PROCEED", "SKIP"]
    skip_reason: Optional[str]
    raw_response: str
    latency_ms: int
    prompt_eval_duration_ms: int
    eval_duration_ms: int


def _load_system_prompt(inventory_summary: str) -> str:
    template = (_PROMPTS_DIR / _PROMPT_NAME).read_text(encoding="utf-8")
    return template.replace("{inventory_summary}", inventory_summary)


def _build_user_message(posting: dict) -> str:
    title = (posting.get("title") or "").strip()
    employer = (posting.get("employer") or "").strip()
    location = (posting.get("location") or "").strip()
    desc_full = posting.get("posting_text") or ""
    desc = desc_full[:_POSTING_DESCRIPTION_TRUNCATE]
    return (
        f"POSTING TO EVALUATE:\n"
        f"Title: {title}\n"
        f"Company: {employer}\n"
        f"Location: {location}\n"
        f"Description: {desc}\n"
    )


def _is_empty_posting(posting: dict) -> bool:
    title = (posting.get("title") or "").strip()
    employer = (posting.get("employer") or "").strip()
    desc = (posting.get("posting_text") or "").strip()
    return not (title or employer or desc)


def _parse_response(raw: str) -> dict:
    """Parse a JSON object out of possibly-noisy model output.

    Tries plain json.loads, then markdown-fence extraction, then a
    greedy {...} grab. Raises ValueError if all three fail.
    """
    if not raw:
        raise ValueError("empty response")
    try:
        return json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        pass
    match = _FENCE_RE.search(raw)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except (ValueError, json.JSONDecodeError):
            pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except (ValueError, json.JSONDecodeError):
            pass
    # Bare-verdict fallback: Gemma 3 4B with think=False sometimes
    # emits just the literal word "PROCEED" or "SKIP" without any
    # JSON wrapper. Synthesize a verdict dict so the filter still
    # works rather than fail-open to PROCEED on real SKIP signal.
    stripped = raw.strip().upper().rstrip(".")
    if stripped in ("PROCEED", "SKIP"):
        return {"verdict": stripped, "skip_reason": None}
    raise ValueError(f"unable to parse JSON from response")


def _proceed(
    raw_response: str,
    latency_ms: int,
    prompt_eval_ms: int,
    eval_ms: int,
) -> Stage2PreResult:
    return Stage2PreResult(
        verdict="PROCEED",
        skip_reason=None,
        raw_response=raw_response,
        latency_ms=latency_ms,
        prompt_eval_duration_ms=prompt_eval_ms,
        eval_duration_ms=eval_ms,
    )


def stage_2pre(
    posting: dict,
    inventory_summary: str,
    profile_config,
    llm_client,
) -> Stage2PreResult:
    """Run the Stage 2pre filter on one posting.

    profile_config is reserved for future use (e.g. profile-aware
    geography lists in the prompt). Currently unused; the prompt
    is hardcoded for the default profile.
    """
    if _is_empty_posting(posting):
        return Stage2PreResult(
            verdict="SKIP",
            skip_reason="OTHER",
            raw_response="",
            latency_ms=0,
            prompt_eval_duration_ms=0,
            eval_duration_ms=0,
        )

    system_prompt = _load_system_prompt(inventory_summary)
    user_message = _build_user_message(posting)

    t0 = time.time()
    try:
        data = llm_client.generate_chat(
            system_prompt=system_prompt,
            user_prompt=user_message,
            temperature=_TEMPERATURE,
            num_predict=_NUM_PREDICT,
            num_ctx=_NUM_CTX,
            model=_DEFAULT_MODEL,
            keep_alive=_KEEP_ALIVE,
            think=False,
        )
    except Exception as e:
        elapsed_ms = int((time.time() - t0) * 1000)
        logger.warning(
            "stage_2pre: generate_chat raised %s; "
            "fail-open -> PROCEED",
            type(e).__name__,
        )
        return _proceed(
            raw_response=f"<exception: {type(e).__name__}>",
            latency_ms=elapsed_ms,
            prompt_eval_ms=0,
            eval_ms=0,
        )

    elapsed_ms = int((time.time() - t0) * 1000)
    raw = data.get("response", "") or ""
    prompt_eval_ms = int(data.get("prompt_eval_duration") or 0) // 1_000_000
    eval_ms = int(data.get("eval_duration") or 0) // 1_000_000

    try:
        parsed = _parse_response(raw)
    except ValueError:
        echo = _try_template_echo(raw)
        if echo is None:
            logger.warning(
                "stage_2pre: parse failure; fail-open -> PROCEED. raw=%r",
                raw[:200],
            )
            return _proceed(raw, elapsed_ms, prompt_eval_ms, eval_ms)
        logger.debug(
            "stage_2pre: template echo detected; extracted verdict=%s",
            echo["verdict"],
        )
        parsed = echo

    verdict_raw = parsed.get("verdict")
    if verdict_raw not in ("PROCEED", "SKIP"):
        logger.warning(
            "stage_2pre: invalid verdict %r; fail-open -> PROCEED",
            verdict_raw,
        )
        return _proceed(raw, elapsed_ms, prompt_eval_ms, eval_ms)

    if verdict_raw == "PROCEED":
        return _proceed(raw, elapsed_ms, prompt_eval_ms, eval_ms)

    skip_reason = parsed.get("skip_reason")
    if skip_reason not in VALID_SKIP_REASONS:
        skip_reason = "OTHER"
    return Stage2PreResult(
        verdict="SKIP",
        skip_reason=skip_reason,
        raw_response=raw,
        latency_ms=elapsed_ms,
        prompt_eval_duration_ms=prompt_eval_ms,
        eval_duration_ms=eval_ms,
    )
