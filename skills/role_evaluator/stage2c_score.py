"""Stage 2c-score: Gemma 4 E4B decomposed scoring.

Scoring-only call. Produces three dimension scores (function,
domain, seniority) each with a quoted evidence span, plus a
binary disqualifier flag with text reason.

ARCHITECTURAL RULE: this call MUST NOT receive nor produce a
counter-argument. The counter-argument is a separate factored
call (stage2c_counter) that runs ONLY when the provisional tier
is STRONG or TOP_TIER. Mixing the two into one call is the
v2.1.0 / Experiment 2 failure mode this pipeline exists to fix.

If you are tempted to add strongest_argument_against_high_rating
to the output schema, the few-shot examples, or this dataclass:
DO NOT. Read the v2.3 architectural rationale before changing it.

DESIGN DECISION (Spec 4 TASK 5, finalized 2026-05-12):
Stage 2c-score is INTENTIONALLY not few-shot-driven. The decomposed
scoring architecture — function + domain + seniority axes scored
independently with explicit rubrics, plus a separate factored
counter-argument verification (stage2c_counter) — is the design
choice. Adding few-shot examples here would re-introduce the
v2.1.0 / Experiment 2 failure mode this pipeline exists to fix.

FewShotSelector in skills/role_evaluator/few_shot.py remains
available for any future Stage 2b-style consumer. Its user_flagged
SKIP weighting (Spec 2 TASK 3) is in place and waiting. The
legacy evaluator.py that previously consumed it was deleted in
Spec 4 TASK 1 (commit 1d56bfa); few_shot.py itself is preserved
because tests/test_flag_feedback.py exercises it directly and the
selector remains correct + tested.

See docs/cleanup_2026-05/dead_code_report.md "Orphaned but
reserved" section for the maintenance rationale.

Parsing is fail-loud: malformed JSON raises Stage2CScoreError.
The pipeline cannot proceed without real numeric scores; defaulting
on parse failure would silently corrupt the score-combine math.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_PROMPT_NAME = "stage2c_score_prompt.txt"
_DEFAULT_MODEL = "gemma4:e4b"
_NUM_PREDICT = 600
_NUM_CTX = 8192
_TEMPERATURE = 0.0
_KEEP_ALIVE = -1
_POSTING_DESCRIPTION_TRUNCATE = 4000

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class Stage2CScoreError(Exception):
    """Raised when the scoring call cannot produce valid scores.

    The decide stage requires real numbers; the score-combine
    function is deterministic and would silently corrupt tier
    decisions if fed defaults. Caller decides retry / skip.
    """


@dataclass(frozen=True)
class Stage2CScoreResult:
    function_score: int
    function_evidence: str
    domain_score: int
    domain_evidence: str
    seniority_score: int
    seniority_evidence: str
    disqualifier_present: bool
    disqualifier_reason: Optional[str]
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
    desc = (posting.get("posting_text") or "")[:_POSTING_DESCRIPTION_TRUNCATE]
    return (
        f"POSTING TO EVALUATE:\n"
        f"Title: {title}\n"
        f"Company: {employer}\n"
        f"Location: {location}\n"
        f"Description: {desc}\n"
    )


def _parse_response(raw: str) -> dict:
    if not raw:
        raise Stage2CScoreError("empty response")
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
    raise Stage2CScoreError(
        f"unable to parse JSON from response: {raw[:200]!r}"
    )


def _clamp_score(value, field: str) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        logger.warning(
            "stage_2c_score: %s = %r is not an int; coercing to 0",
            field, value,
        )
        return 0
    if n < 0:
        logger.warning(
            "stage_2c_score: %s = %d below 0; clamping to 0",
            field, n,
        )
        return 0
    if n > 3:
        logger.warning(
            "stage_2c_score: %s = %d above 3; clamping to 3",
            field, n,
        )
        return 3
    return n


def stage_2c_score(
    posting: dict,
    inventory_summary: str,
    profile_config,
    llm_client,
) -> Stage2CScoreResult:
    """Run the Stage 2c-score call on one posting.

    Returns a Stage2CScoreResult with three dimension scores +
    evidence quotes + disqualifier flag.

    Raises Stage2CScoreError on parse failure or when required
    numeric fields are missing/malformed beyond clamp recovery.
    """
    system_prompt = _load_system_prompt(inventory_summary)
    user_message = _build_user_message(posting)

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

    parsed = _parse_response(raw)

    function_score = _clamp_score(
        parsed.get("function_score"), "function_score",
    )
    domain_score = _clamp_score(
        parsed.get("domain_score"), "domain_score",
    )
    seniority_score = _clamp_score(
        parsed.get("seniority_score"), "seniority_score",
    )

    disq_present = bool(parsed.get("disqualifier_present"))
    disq_reason_raw = parsed.get("disqualifier_reason")
    disq_reason: Optional[str]
    if disq_reason_raw is None:
        disq_reason = None
    else:
        disq_reason = str(disq_reason_raw).strip() or None

    return Stage2CScoreResult(
        function_score=function_score,
        function_evidence=str(parsed.get("function_evidence") or "").strip(),
        domain_score=domain_score,
        domain_evidence=str(parsed.get("domain_evidence") or "").strip(),
        seniority_score=seniority_score,
        seniority_evidence=str(
            parsed.get("seniority_evidence") or ""
        ).strip(),
        disqualifier_present=disq_present,
        disqualifier_reason=disq_reason,
        raw_response=raw,
        latency_ms=elapsed_ms,
        prompt_eval_duration_ms=prompt_eval_ms,
        eval_duration_ms=eval_ms,
    )
