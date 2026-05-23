"""Stage 2c-counter: factored counter-argument verification.

CoVe (Chain-of-Verification) factored variant. Architecturally
separate from the scoring call so the model cannot anchor on its
own prior reasoning.

NON-NEGOTIABLE: this call receives ONLY the numeric scores from
the scoring call. It does NOT receive:
  - function_evidence, domain_evidence, seniority_evidence
  - disqualifier_reason text
  - any reasoning the model generated previously

It receives:
  - the raw posting (same input as the scoring call)
  - the inventory summary
  - the 4 numeric/bool scores: function_score, domain_score,
    seniority_score, disqualifier_present

Mixing reasoning into this call defeats the architectural fix
that the v2.3 pipeline exists to deliver. Don't add fields.

counter_is_substantive is computed in Python via is_substantive(),
NOT by the LLM. The threshold (>= 30 chars after stripping the
known-no-counter sentinels) is deliberately conservative — short
or vague counters do not warrant a tier downgrade.

Soft-fail on parse error: returns counter_is_substantive=False so
the score-combine step does not downgrade. We don't know whether
there was a real counter; defaulting to "no" preserves shortlists.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_PROMPT_NAME = "stage2c_counter_prompt.txt"
_DEFAULT_MODEL = "gemma4:e4b"
_NUM_PREDICT = 300
_NUM_CTX = 8192
_TEMPERATURE = 0.0
_KEEP_ALIVE = -1
_POSTING_DESCRIPTION_TRUNCATE = 4000
_SUBSTANTIVE_MIN_LEN = 30

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

_NON_SUBSTANTIVE_TOKENS = (
    "no_substantive_counter",
    "none",
    "n/a",
    "",
)


@dataclass(frozen=True)
class Stage2CCounterResult:
    strongest_argument_against: str
    counter_is_substantive: bool
    raw_response: str
    latency_ms: int
    prompt_eval_duration_ms: int
    eval_duration_ms: int


def is_substantive(text: str) -> bool:
    """Return True iff the text is a real counter-argument.

    Rejects empty / sentinel ('no_substantive_counter', 'none',
    'n/a') / too-short (<30 chars) text. Case-insensitive.
    """
    if not text:
        return False
    t = text.strip().lower()
    if t in _NON_SUBSTANTIVE_TOKENS:
        return False
    if len(t) < _SUBSTANTIVE_MIN_LEN:
        return False
    return True


def _load_system_prompt(inventory_summary: str) -> str:
    template = (_PROMPTS_DIR / _PROMPT_NAME).read_text(encoding="utf-8")
    return template.replace("{inventory_summary}", inventory_summary)


def _build_user_message(posting: dict, prior_scores: dict) -> str:
    """Build the user message.

    NOTE: only the 4 numeric/bool keys are extracted from
    prior_scores. Any other keys (e.g. function_evidence,
    disqualifier_reason) are deliberately ignored to preserve the
    factored-verification architectural protection.
    """
    fn = int(prior_scores.get("function_score", 0))
    dom = int(prior_scores.get("domain_score", 0))
    sen = int(prior_scores.get("seniority_score", 0))
    disq = bool(prior_scores.get("disqualifier_present", False))

    title = (posting.get("title") or "").strip()
    employer = (posting.get("employer") or "").strip()
    location = (posting.get("location") or "").strip()
    desc = (posting.get("posting_text") or "")[:_POSTING_DESCRIPTION_TRUNCATE]

    return (
        f"PRIOR SCORES (you have NOT seen the reasoning behind these):\n"
        f"Function score: {fn}/3\n"
        f"Domain score: {dom}/3\n"
        f"Seniority score: {sen}/3\n"
        f"Disqualifier present: {disq}\n"
        f"\n"
        f"POSTING TO EVALUATE:\n"
        f"Title: {title}\n"
        f"Company: {employer}\n"
        f"Location: {location}\n"
        f"Description: {desc}\n"
    )


def _parse_response(raw: str) -> dict:
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
    raise ValueError(f"unable to parse JSON from response")


def stage_2c_counter(
    posting: dict,
    inventory_summary: str,
    profile_config,
    llm_client,
    prior_scores: dict,
) -> Stage2CCounterResult:
    """Run the factored counter-argument call.

    Soft-fail on parse error: returns counter_is_substantive=False
    so score-combine does not downgrade.
    """
    system_prompt = _load_system_prompt(inventory_summary)
    user_message = _build_user_message(posting, prior_scores)

    t0 = time.time()
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
    elapsed_ms = int((time.time() - t0) * 1000)
    raw = data.get("response", "") or ""
    prompt_eval_ms = int(data.get("prompt_eval_duration") or 0) // 1_000_000
    eval_ms = int(data.get("eval_duration") or 0) // 1_000_000

    try:
        parsed = _parse_response(raw)
        counter = str(
            parsed.get("strongest_argument_against_high_rating") or ""
        ).strip()
    except ValueError:
        logger.warning(
            "stage_2c_counter: parse failure; treating as "
            "no_substantive_counter. raw=%r",
            raw[:200],
        )
        counter = ""

    return Stage2CCounterResult(
        strongest_argument_against=counter,
        counter_is_substantive=is_substantive(counter),
        raw_response=raw,
        latency_ms=elapsed_ms,
        prompt_eval_duration_ms=prompt_eval_ms,
        eval_duration_ms=eval_ms,
    )
