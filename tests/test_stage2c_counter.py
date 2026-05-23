"""Unit tests for skills.role_evaluator.stage2c_counter.

Mocks LLMClient.generate_chat - no live Ollama.

Architectural guardrail: the user message sent to the LLM must
contain ONLY the 4 numeric/bool prior_scores values, never the
evidence quotes or disqualifier_reason text from the scoring call.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from skills.role_evaluator.stage2c_counter import (  # noqa: E402
    Stage2CCounterResult,
    is_substantive,
    stage_2c_counter,
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


def _scores(fn: int = 3, dom: int = 3, sen: int = 3, disq: bool = False) -> dict:
    return {
        "function_score": fn,
        "domain_score": dom,
        "seniority_score": sen,
        "disqualifier_present": disq,
    }


# --- Behavioral routing ---------------------------------------------

def test_produces_substantive_argument_on_mismatch():
    counter_text = (
        "The role is fundamentally advisory, not delivery. "
        "Vocabulary overlap does not bridge the functional gap."
    )
    llm = _fake_llm(
        '{"strongest_argument_against_high_rating": '
        + repr(counter_text).replace("'", '"') + '}'
    )
    result = stage_2c_counter(
        _posting(), "INV", None, llm, prior_scores=_scores(),
    )
    assert isinstance(result, Stage2CCounterResult)
    assert result.strongest_argument_against == counter_text
    assert result.counter_is_substantive is True


def test_returns_no_substantive_on_clean_match():
    llm = _fake_llm(
        '{"strongest_argument_against_high_rating": "no_substantive_counter"}'
    )
    result = stage_2c_counter(
        _posting(), "INV", None, llm, prior_scores=_scores(),
    )
    assert result.strongest_argument_against == "no_substantive_counter"
    assert result.counter_is_substantive is False


# --- ARCHITECTURAL GUARDRAIL: information leak ----------------------

def test_does_not_receive_evidence_quotes():
    """Even if caller passes evidence quotes in prior_scores dict,
    none of that text may appear in the user_prompt sent to the LLM.
    The factored verification depends on this isolation.
    """
    llm = _fake_llm(
        '{"strongest_argument_against_high_rating": "no_substantive_counter"}'
    )
    prior = {
        "function_score": 3,
        "function_evidence": "UNIQUE_FN_LEAK_TOKEN",
        "domain_score": 3,
        "domain_evidence": "UNIQUE_DOM_LEAK_TOKEN",
        "seniority_score": 3,
        "seniority_evidence": "UNIQUE_SEN_LEAK_TOKEN",
        "disqualifier_present": False,
    }
    stage_2c_counter(_posting(), "INV", None, llm, prior_scores=prior)
    user_prompt = _kwargs(llm)["user_prompt"]
    assert "UNIQUE_FN_LEAK_TOKEN" not in user_prompt
    assert "UNIQUE_DOM_LEAK_TOKEN" not in user_prompt
    assert "UNIQUE_SEN_LEAK_TOKEN" not in user_prompt


def test_does_not_receive_disqualifier_reason():
    llm = _fake_llm(
        '{"strongest_argument_against_high_rating": "no_substantive_counter"}'
    )
    prior = {
        "function_score": 2,
        "domain_score": 2,
        "seniority_score": 3,
        "disqualifier_present": True,
        "disqualifier_reason":
            "UNIQUE_DISQ_LEAK_TOKEN: Mandatory BlackLine certification",
    }
    stage_2c_counter(_posting(), "INV", None, llm, prior_scores=prior)
    user_prompt = _kwargs(llm)["user_prompt"]
    assert "UNIQUE_DISQ_LEAK_TOKEN" not in user_prompt
    # The bool itself is correctly conveyed:
    assert "Disqualifier present: True" in user_prompt


def test_prior_scores_are_numeric_only():
    """Verify the user message contains exactly the 4 numeric/bool
    scores in the documented format, and nothing else from
    prior_scores."""
    llm = _fake_llm(
        '{"strongest_argument_against_high_rating": "no_substantive_counter"}'
    )
    prior = _scores(fn=1, dom=2, sen=3, disq=False)
    stage_2c_counter(_posting(), "INV", None, llm, prior_scores=prior)
    user_prompt = _kwargs(llm)["user_prompt"]
    assert "Function score: 1/3" in user_prompt
    assert "Domain score: 2/3" in user_prompt
    assert "Seniority score: 3/3" in user_prompt
    assert "Disqualifier present: False" in user_prompt


def test_disqualifier_present_true_propagates():
    llm = _fake_llm(
        '{"strongest_argument_against_high_rating": "no_substantive_counter"}'
    )
    stage_2c_counter(
        _posting(), "INV", None, llm,
        prior_scores=_scores(disq=True),
    )
    user_prompt = _kwargs(llm)["user_prompt"]
    assert "Disqualifier present: True" in user_prompt


# --- is_substantive helper ------------------------------------------

def test_is_substantive_rejects_short_text():
    assert is_substantive("short") is False
    assert is_substantive("less than 30 chars here.") is False


def test_is_substantive_rejects_no_substantive_counter():
    assert is_substantive("no_substantive_counter") is False
    assert is_substantive("NO_SUBSTANTIVE_COUNTER") is False
    assert is_substantive("  no_substantive_counter  ") is False


def test_is_substantive_rejects_none_and_na():
    assert is_substantive("none") is False
    assert is_substantive("None") is False
    assert is_substantive("N/A") is False
    assert is_substantive("n/a") is False


def test_is_substantive_rejects_empty():
    assert is_substantive("") is False
    assert is_substantive("   ") is False


def test_is_substantive_accepts_30_plus_chars():
    text = "The candidate lacks the required certification for this role"
    assert len(text) >= 30
    assert is_substantive(text) is True


def test_is_substantive_boundary_30_chars():
    """Exactly 30 chars after stripping is on the accept side."""
    text = "x" * 30
    assert is_substantive(text) is True
    text29 = "x" * 29
    assert is_substantive(text29) is False


# --- Soft-fail parsing ----------------------------------------------

def test_handles_malformed_json_returns_non_substantive():
    """Soft-fail: parse error -> counter_is_substantive=False so
    score-combine does not downgrade."""
    llm = _fake_llm("the model just rambled prose without any json")
    result = stage_2c_counter(
        _posting(), "INV", None, llm, prior_scores=_scores(),
    )
    assert result.counter_is_substantive is False
    assert result.strongest_argument_against == ""


def test_handles_markdown_fenced_json():
    counter = "Role is fundamentally advisory, not hands-on delivery work."
    llm = _fake_llm(
        '```json\n'
        '{"strongest_argument_against_high_rating": "' + counter + '"}\n'
        '```'
    )
    result = stage_2c_counter(
        _posting(), "INV", None, llm, prior_scores=_scores(),
    )
    assert result.strongest_argument_against == counter
    assert result.counter_is_substantive is True


# --- Call configuration ---------------------------------------------

def test_temperature_is_zero():
    llm = _fake_llm(
        '{"strongest_argument_against_high_rating": "no_substantive_counter"}'
    )
    stage_2c_counter(_posting(), "INV", None, llm, prior_scores=_scores())
    assert _kwargs(llm)["temperature"] == 0.0


def test_num_predict_capped_at_300():
    llm = _fake_llm(
        '{"strongest_argument_against_high_rating": "no_substantive_counter"}'
    )
    stage_2c_counter(_posting(), "INV", None, llm, prior_scores=_scores())
    assert _kwargs(llm)["num_predict"] == 300


def test_num_ctx_is_8192():
    llm = _fake_llm(
        '{"strongest_argument_against_high_rating": "no_substantive_counter"}'
    )
    stage_2c_counter(_posting(), "INV", None, llm, prior_scores=_scores())
    assert _kwargs(llm)["num_ctx"] == 8192


def test_keep_alive_is_minus_one():
    llm = _fake_llm(
        '{"strongest_argument_against_high_rating": "no_substantive_counter"}'
    )
    stage_2c_counter(_posting(), "INV", None, llm, prior_scores=_scores())
    assert _kwargs(llm)["keep_alive"] == -1


def test_passes_think_false():
    """Stage 2c-counter must disable thinking on gemma4:e4b."""
    llm = _fake_llm(
        '{"strongest_argument_against_high_rating": "no_substantive_counter"}'
    )
    stage_2c_counter(_posting(), "INV", None, llm, prior_scores=_scores())
    assert _kwargs(llm)["think"] is False


def test_uses_decide_model():
    llm = _fake_llm(
        '{"strongest_argument_against_high_rating": "no_substantive_counter"}'
    )
    stage_2c_counter(_posting(), "INV", None, llm, prior_scores=_scores())
    assert _kwargs(llm)["model"] == "gemma4:e4b"


def test_uses_generate_chat_not_generate():
    llm = MagicMock()
    llm.generate_chat.return_value = {
        "response":
            '{"strongest_argument_against_high_rating": "no_substantive_counter"}',
        "prompt_eval_duration": 0,
        "eval_duration": 0,
    }
    stage_2c_counter(_posting(), "INV", None, llm, prior_scores=_scores())
    llm.generate_chat.assert_called_once()
    llm.generate.assert_not_called()


def test_inventory_summary_in_system_message():
    llm = _fake_llm(
        '{"strongest_argument_against_high_rating": "no_substantive_counter"}'
    )
    stage_2c_counter(
        _posting(), "MY_UNIQUE_INVENTORY_TOKEN_xyz", None, llm,
        prior_scores=_scores(),
    )
    assert (
        "MY_UNIQUE_INVENTORY_TOKEN_xyz"
        in _kwargs(llm)["system_prompt"]
    )


# --- Latency --------------------------------------------------------

def test_latency_recorded():
    llm = _fake_llm(
        '{"strongest_argument_against_high_rating": "no_substantive_counter"}',
        prompt_eval_ns=4_000_000_000,
        eval_ns=20_000_000_000,
    )
    result = stage_2c_counter(
        _posting(), "INV", None, llm, prior_scores=_scores(),
    )
    assert result.prompt_eval_duration_ms == 4000
    assert result.eval_duration_ms == 20000
    assert result.latency_ms >= 0
