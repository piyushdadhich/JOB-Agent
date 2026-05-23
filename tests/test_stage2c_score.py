"""Unit tests for skills.role_evaluator.stage2c_score.

Mocks LLMClient.generate_chat - no live Ollama.

Architectural guardrail: this stage MUST NOT produce or receive a
counter-argument. test_no_counter_argument_field_in_output enforces
that.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from skills.role_evaluator.stage2c_score import (  # noqa: E402
    Stage2CScoreError,
    Stage2CScoreResult,
    stage_2c_score,
)


# --- helpers ---------------------------------------------------------

def _fake_llm(
    content: str,
    prompt_eval_ns: int = 0,
    eval_ns: int = 0,
) -> MagicMock:
    llm = MagicMock()
    llm.generate_chat.return_value = {
        "response": content,
        "prompt_eval_duration": prompt_eval_ns,
        "eval_duration": eval_ns,
    }
    return llm


def _posting(
    title: str = "Senior Project Manager",
    employer: str = "Acme Corp",
    location: str = "Toronto, ON",
    text: str = "Lead delivery of complex programs.",
) -> dict:
    return {
        "id": 1,
        "title": title,
        "employer": employer,
        "location": location,
        "posting_text": text,
    }


def _kwargs(llm: MagicMock) -> dict:
    _, kwargs = llm.generate_chat.call_args
    return kwargs


def _valid_score_json(
    function_score: int = 2,
    domain_score: int = 2,
    seniority_score: int = 3,
    disq_present: bool = False,
    disq_reason: str = "null",
) -> str:
    return (
        '{'
        f'"function_score": {function_score}, '
        '"function_evidence": "lead delivery of programs", '
        f'"domain_score": {domain_score}, '
        '"domain_evidence": "financial services", '
        f'"seniority_score": {seniority_score}, '
        '"seniority_evidence": "Senior", '
        f'"disqualifier_present": {str(disq_present).lower()}, '
        f'"disqualifier_reason": {disq_reason}'
        '}'
    )


# --- Behavioral: scoring routes -------------------------------------

def test_function_score_0_on_audit_role():
    """Audit posting with delivery vocabulary should score function=0
    when the model correctly identifies the trap."""
    llm = _fake_llm(_valid_score_json(function_score=0))
    result = stage_2c_score(_posting(title="Senior Internal Auditor"),
                            "INV", None, llm)
    assert result.function_score == 0


def test_function_score_3_on_direct_delivery_match():
    llm = _fake_llm(_valid_score_json(function_score=3))
    result = stage_2c_score(_posting(), "INV", None, llm)
    assert result.function_score == 3


def test_domain_score_independent_of_function():
    """A posting can have function=0 but domain=3 (industry match
    even though the work itself is wrong). The two scores must
    be carried independently to the output."""
    llm = _fake_llm(_valid_score_json(function_score=0, domain_score=3))
    result = stage_2c_score(_posting(), "INV", None, llm)
    assert result.function_score == 0
    assert result.domain_score == 3


def test_seniority_score_independent_of_function():
    llm = _fake_llm(_valid_score_json(function_score=0, seniority_score=3))
    result = stage_2c_score(_posting(), "INV", None, llm)
    assert result.function_score == 0
    assert result.seniority_score == 3


def test_disqualifier_detected_blackline():
    raw = _valid_score_json(
        disq_present=True,
        disq_reason='"Mandatory 5+ years BlackLine experience"',
    )
    llm = _fake_llm(raw)
    result = stage_2c_score(_posting(), "INV", None, llm)
    assert result.disqualifier_present is True
    assert "BlackLine" in result.disqualifier_reason


def test_no_disqualifier_on_clean_match():
    llm = _fake_llm(_valid_score_json(disq_present=False))
    result = stage_2c_score(_posting(), "INV", None, llm)
    assert result.disqualifier_present is False
    assert result.disqualifier_reason is None


def test_evidence_fields_populated():
    llm = _fake_llm(_valid_score_json())
    result = stage_2c_score(_posting(), "INV", None, llm)
    assert result.function_evidence == "lead delivery of programs"
    assert result.domain_evidence == "financial services"
    assert result.seniority_evidence == "Senior"


# --- Architectural guardrail: NO counter-argument -------------------

def test_no_counter_argument_field_in_output():
    """Stage 2c-score must not have any counter-argument fields.

    Counter-argument is the factored Stage 2c-counter call.
    Mixing them defeats the v2.3 architectural fix.
    """
    llm = _fake_llm(_valid_score_json())
    result = stage_2c_score(_posting(), "INV", None, llm)
    forbidden = (
        "strongest_argument_against_high_rating",
        "strongest_argument_against",
        "counter_argument",
        "counter_is_substantive",
        "counter_text",
    )
    for name in forbidden:
        assert not hasattr(result, name), (
            f"Stage2CScoreResult must not expose {name}; "
            f"counter-argument is a factored separate call."
        )


def test_system_prompt_does_not_request_counter_argument():
    """The prompt template must not ask the model for a counter-arg.

    If the model is instructed to produce one, the architectural
    separation is broken even if our parser ignores the field.
    """
    llm = _fake_llm(_valid_score_json())
    stage_2c_score(_posting(), "INV", None, llm)
    system_prompt = _kwargs(llm)["system_prompt"]
    forbidden_substrings = (
        "strongest_argument_against",
        "counter-argument",
        "counter argument",
    )
    for s in forbidden_substrings:
        assert s not in system_prompt, (
            f"System prompt must not mention {s!r}"
        )


# --- Fail-loud parsing ---------------------------------------------

def test_handles_malformed_json_raises_error():
    llm = _fake_llm("the model just rambled prose without any json")
    with pytest.raises(Stage2CScoreError):
        stage_2c_score(_posting(), "INV", None, llm)


def test_empty_response_raises_error():
    llm = _fake_llm("")
    with pytest.raises(Stage2CScoreError):
        stage_2c_score(_posting(), "INV", None, llm)


def test_handles_markdown_fenced_json():
    llm = _fake_llm(
        "```json\n" + _valid_score_json(function_score=2) + "\n```"
    )
    result = stage_2c_score(_posting(), "INV", None, llm)
    assert result.function_score == 2


# --- Score clamping ------------------------------------------------

def test_score_clamped_to_0_3_range_high():
    llm = _fake_llm(_valid_score_json(function_score=99))
    result = stage_2c_score(_posting(), "INV", None, llm)
    assert result.function_score == 3


def test_score_clamped_to_0_3_range_low():
    llm = _fake_llm(_valid_score_json(domain_score=-5))
    result = stage_2c_score(_posting(), "INV", None, llm)
    assert result.domain_score == 0


def test_non_int_score_coerced_to_zero():
    """If the model emits a non-numeric score, we coerce to 0
    rather than raise — the disqualifier path can still salvage
    the verdict."""
    raw = (
        '{"function_score": "high", '
        '"function_evidence": "x", '
        '"domain_score": 2, "domain_evidence": "x", '
        '"seniority_score": 3, "seniority_evidence": "x", '
        '"disqualifier_present": false, "disqualifier_reason": null}'
    )
    llm = _fake_llm(raw)
    result = stage_2c_score(_posting(), "INV", None, llm)
    assert result.function_score == 0


# --- Call configuration --------------------------------------------

def test_temperature_is_zero():
    llm = _fake_llm(_valid_score_json())
    stage_2c_score(_posting(), "INV", None, llm)
    assert _kwargs(llm)["temperature"] == 0.0


def test_num_predict_capped_at_600():
    llm = _fake_llm(_valid_score_json())
    stage_2c_score(_posting(), "INV", None, llm)
    assert _kwargs(llm)["num_predict"] == 600


def test_num_ctx_is_8192():
    llm = _fake_llm(_valid_score_json())
    stage_2c_score(_posting(), "INV", None, llm)
    assert _kwargs(llm)["num_ctx"] == 8192


def test_keep_alive_is_minus_one():
    llm = _fake_llm(_valid_score_json())
    stage_2c_score(_posting(), "INV", None, llm)
    assert _kwargs(llm)["keep_alive"] == -1


def test_passes_think_false():
    """Stage 2c-score must disable thinking. gemma4:e4b otherwise
    silently consumes num_predict on a reasoning trace and emits
    empty content."""
    llm = _fake_llm(_valid_score_json())
    stage_2c_score(_posting(), "INV", None, llm)
    assert _kwargs(llm)["think"] is False


def test_uses_decide_model():
    llm = _fake_llm(_valid_score_json())
    stage_2c_score(_posting(), "INV", None, llm)
    assert _kwargs(llm)["model"] == "gemma4:e4b"


def test_uses_generate_chat_not_generate():
    llm = MagicMock()
    llm.generate_chat.return_value = {
        "response": _valid_score_json(),
        "prompt_eval_duration": 0,
        "eval_duration": 0,
    }
    stage_2c_score(_posting(), "INV", None, llm)
    llm.generate_chat.assert_called_once()
    llm.generate.assert_not_called()


def test_inventory_summary_in_system_message():
    llm = _fake_llm(_valid_score_json())
    stage_2c_score(_posting(), "MY_UNIQUE_INVENTORY_TOKEN_xyz", None, llm)
    assert (
        "MY_UNIQUE_INVENTORY_TOKEN_xyz"
        in _kwargs(llm)["system_prompt"]
    )


# --- Latency ------------------------------------------------------

def test_latency_recorded():
    llm = _fake_llm(
        _valid_score_json(),
        prompt_eval_ns=8_000_000_000,
        eval_ns=40_000_000_000,
    )
    result = stage_2c_score(_posting(), "INV", None, llm)
    assert result.prompt_eval_duration_ms == 8000
    assert result.eval_duration_ms == 40000
    assert result.latency_ms >= 0
