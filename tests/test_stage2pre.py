"""Unit tests for skills.role_evaluator.stage2pre.

Mocks LLMClient.generate_chat — no live Ollama.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from skills.role_evaluator.stage2pre import (  # noqa: E402
    Stage2PreResult,
    stage_2pre,
)
from skills.role_evaluator import stage2pre as _s2pre_module  # noqa: E402


def _fake_llm(
    content: str,
    prompt_eval_ns: int = 0,
    eval_ns: int = 0,
) -> MagicMock:
    """Mock LLMClient whose generate_chat returns a controllable dict."""
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


# --- Verdict & skip_reason routing -------------------------------------

def test_proceeds_on_obvious_match():
    llm = _fake_llm('{"verdict": "PROCEED", "skip_reason": null}')
    result = stage_2pre(_posting(), "INV", profile_config=None, llm_client=llm)
    assert isinstance(result, Stage2PreResult)
    assert result.verdict == "PROCEED"
    assert result.skip_reason is None


def test_skips_on_software_engineering_role():
    llm = _fake_llm(
        '{"verdict": "SKIP", "skip_reason": "NO_FUNCTIONAL_OVERLAP"}'
    )
    result = stage_2pre(
        _posting(title="Senior Backend Engineer", text="Write Go code."),
        "INV", None, llm,
    )
    assert result.verdict == "SKIP"
    assert result.skip_reason == "NO_FUNCTIONAL_OVERLAP"


def test_skips_on_frontline_individual_contributor():
    llm = _fake_llm(
        '{"verdict": "SKIP", "skip_reason": "WRONG_SENIORITY"}'
    )
    result = stage_2pre(
        _posting(title="Junior Project Coordinator"),
        "INV", None, llm,
    )
    assert result.verdict == "SKIP"
    assert result.skip_reason == "WRONG_SENIORITY"


def test_skips_on_geo_mismatch():
    llm = _fake_llm(
        '{"verdict": "SKIP", "skip_reason": "GEO_MISMATCH"}'
    )
    result = stage_2pre(
        _posting(location="Tulsa, OK"),
        "INV", None, llm,
    )
    assert result.verdict == "SKIP"
    assert result.skip_reason == "GEO_MISMATCH"


def test_proceeds_when_in_doubt():
    llm = _fake_llm('{"verdict": "PROCEED", "skip_reason": null}')
    result = stage_2pre(
        _posting(title="Senior Manager, Operations Transformation"),
        "INV", None, llm,
    )
    assert result.verdict == "PROCEED"


# --- Fail-open behavior ------------------------------------------------

def test_handles_malformed_llm_response_defaults_to_proceed():
    """Unparseable JSON -> PROCEED (fail-open)."""
    llm = _fake_llm("the model just rambled prose without any json")
    result = stage_2pre(_posting(), "INV", None, llm)
    assert result.verdict == "PROCEED"
    assert result.skip_reason is None


def test_handles_invalid_verdict_value_defaults_to_proceed():
    llm = _fake_llm('{"verdict": "MAYBE", "skip_reason": null}')
    result = stage_2pre(_posting(), "INV", None, llm)
    assert result.verdict == "PROCEED"


def test_handles_llm_exception_defaults_to_proceed():
    llm = MagicMock()
    llm.generate_chat.side_effect = ConnectionError("ollama down")
    result = stage_2pre(_posting(), "INV", None, llm)
    assert result.verdict == "PROCEED"
    assert "exception" in result.raw_response


def test_handles_empty_posting_skips():
    """No title/employer/text -> SKIP/OTHER, no LLM call."""
    llm = MagicMock()
    posting = {"id": 999, "title": "", "employer": "", "posting_text": ""}
    result = stage_2pre(posting, "INV", None, llm)
    assert result.verdict == "SKIP"
    assert result.skip_reason == "OTHER"
    llm.generate_chat.assert_not_called()


def test_invalid_skip_reason_coerced_to_other():
    llm = _fake_llm('{"verdict": "SKIP", "skip_reason": "BANANAS"}')
    result = stage_2pre(_posting(), "INV", None, llm)
    assert result.verdict == "SKIP"
    assert result.skip_reason == "OTHER"


def test_handles_markdown_fenced_json():
    llm = _fake_llm(
        '```json\n{"verdict": "SKIP", "skip_reason": "OTHER"}\n```'
    )
    result = stage_2pre(_posting(), "INV", None, llm)
    assert result.verdict == "SKIP"
    assert result.skip_reason == "OTHER"


def test_bare_verdict_string_skip_accepted():
    """Gemma 3 4B with think=False sometimes emits literally 'SKIP'
    or 'PROCEED' with no JSON. Parser must accept this rather than
    fail-open and silently swallow the signal."""
    llm = _fake_llm("SKIP\n")
    result = stage_2pre(_posting(), "INV", None, llm)
    assert result.verdict == "SKIP"
    assert result.skip_reason == "OTHER"


def test_bare_verdict_string_proceed_accepted():
    llm = _fake_llm("PROCEED")
    result = stage_2pre(_posting(), "INV", None, llm)
    assert result.verdict == "PROCEED"
    assert result.skip_reason is None


def test_bare_verdict_lowercase_accepted():
    llm = _fake_llm("  proceed  ")
    result = stage_2pre(_posting(), "INV", None, llm)
    assert result.verdict == "PROCEED"


# --- Skip-reason forcing on PROCEED -----------------------------------

def test_skip_reason_forced_null_on_proceed():
    """Even if model fills skip_reason on PROCEED, we nullify it.
    Gemma 3 4B Q4 has been observed putting reasoning text in
    skip_reason when verdict=PROCEED.
    """
    llm = _fake_llm(
        '{"verdict": "PROCEED", '
        '"skip_reason": "Strong match in financial services"}'
    )
    result = stage_2pre(_posting(), "INV", None, llm)
    assert result.verdict == "PROCEED"
    assert result.skip_reason is None


# --- Latency recording -------------------------------------------------

def test_latency_recorded():
    llm = _fake_llm(
        '{"verdict": "PROCEED", "skip_reason": null}',
        prompt_eval_ns=2_000_000_000,
        eval_ns=500_000_000,
    )
    result = stage_2pre(_posting(), "INV", None, llm)
    assert result.prompt_eval_duration_ms == 2000
    assert result.eval_duration_ms == 500
    assert result.latency_ms >= 0


# --- Call configuration ------------------------------------------------

def _kwargs(llm: MagicMock) -> dict:
    _, kwargs = llm.generate_chat.call_args
    return kwargs


def test_temperature_is_zero():
    llm = _fake_llm('{"verdict": "PROCEED", "skip_reason": null}')
    stage_2pre(_posting(), "INV", None, llm)
    assert _kwargs(llm)["temperature"] == 0.0


def test_num_predict_capped_at_200():
    llm = _fake_llm('{"verdict": "PROCEED", "skip_reason": null}')
    stage_2pre(_posting(), "INV", None, llm)
    assert _kwargs(llm)["num_predict"] == 200


def test_num_ctx_is_4096():
    llm = _fake_llm('{"verdict": "PROCEED", "skip_reason": null}')
    stage_2pre(_posting(), "INV", None, llm)
    assert _kwargs(llm)["num_ctx"] == 4096


def test_keep_alive_is_minus_one():
    llm = _fake_llm('{"verdict": "PROCEED", "skip_reason": null}')
    stage_2pre(_posting(), "INV", None, llm)
    assert _kwargs(llm)["keep_alive"] == -1


def test_passes_think_false():
    """Stage 2pre must disable thinking. Without it, gemma4-class
    thinking models consume the num_predict budget on a reasoning
    trace and return empty content (see scripts/output/fast_eval_v2_3
    failure trace)."""
    llm = _fake_llm('{"verdict": "PROCEED", "skip_reason": null}')
    stage_2pre(_posting(), "INV", None, llm)
    assert _kwargs(llm)["think"] is False


def test_uses_filter_model():
    llm = _fake_llm('{"verdict": "PROCEED", "skip_reason": null}')
    stage_2pre(_posting(), "INV", None, llm)
    assert _kwargs(llm)["model"] == "gemma3-4b-ctx4k"


def test_uses_generate_chat_not_generate():
    llm = MagicMock()
    llm.generate_chat.return_value = {
        "response": '{"verdict": "PROCEED", "skip_reason": null}',
        "prompt_eval_duration": 0,
        "eval_duration": 0,
    }
    stage_2pre(_posting(), "INV", None, llm)
    llm.generate_chat.assert_called_once()
    llm.generate.assert_not_called()


def test_inventory_summary_in_system_prompt():
    llm = _fake_llm('{"verdict": "PROCEED", "skip_reason": null}')
    stage_2pre(_posting(), "MY_UNIQUE_INVENTORY_TOKEN_xyz", None, llm)
    assert "MY_UNIQUE_INVENTORY_TOKEN_xyz" in _kwargs(llm)["system_prompt"]


# --- Template-echo defense (PROBLEM 1) ---------------------------------

@pytest.mark.parametrize(
    "echo_raw, expected_verdict",
    [
        # Verbatim copy of the old pipe schema with verdict=PROCEED
        (
            '{"verdict": "PROCEED", "skip_reason": null | '
            '"NO_FUNCTIONAL_OVERLAP" | "WRONG_SENIORITY" | '
            '"GEO_MISMATCH" | "OTHER"}',
            "PROCEED",
        ),
        # Echo with verdict=SKIP — taken from a real run
        (
            '{"verdict": "SKIP", "skip_reason": null | '
            '"NO_FUNCTIONAL_OVERLAP" | "WRONG_SENIORITY"}',
            "SKIP",
        ),
        # Echo with reasoning suffix bleeding past `OTHER` (also seen)
        (
            '{"verdict": "PROCEED", "skip_reason": null | '
            '"NO_FUNCTIONAL_OVERLAP" | "OTHER": "looks like a fit"',
            "PROCEED",
        ),
    ],
)
def test_stage2pre_template_echo_detected(
    echo_raw, expected_verdict, caplog,
):
    """Template-echo is recovered as the right verdict and logged at
    DEBUG, not WARNING — defense-in-depth so a future prompt-template
    regression doesn't silently fail-open every call."""
    llm = _fake_llm(echo_raw)
    with caplog.at_level(
        logging.DEBUG, logger=_s2pre_module.logger.name,
    ):
        result = stage_2pre(_posting(), "INV", None, llm)
    assert result.verdict == expected_verdict
    # No "parse failure" WARNING — that would mean we fell through
    # to fail-open instead of recovering the verdict.
    parse_failure_warnings = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING
        and "parse failure" in r.getMessage()
    ]
    assert parse_failure_warnings == []
    # The echo branch logs at DEBUG.
    debug_msgs = [
        r.getMessage() for r in caplog.records
        if r.levelno == logging.DEBUG
    ]
    assert any("template echo" in m for m in debug_msgs)


def test_stage2pre_template_echo_handler_present():
    """The prompt deliberately keeps the pipe-separated output schema
    that Gemma sometimes echoes verbatim. _try_template_echo() in
    stage2pre.py is what makes the echo survivable — without it
    we silently fail-open every echoed call.

    The schema was retained after four prompt variants were tested
    against the 30-posting fast eval set on 2026-05-06:
      - Pipe schema + echo handler:           Gate B  7-8/10 (baseline)
      - Plain concrete-example output:        Gate B  4/10
      - Concrete + PROCEED-bias retune:       Gate B  5/10
      - Concrete + few-shot calibration:      Gate B  4/10

    Every alternative tanked Gate B by exposing Gemma's underlying
    SKIP bias, which the fail-open path on echo had been masking.
    Fixing the prompt also breaks the silent rescue, so we keep
    both: schema as it was, and echo handler as defense-in-depth.
    A future move to a different prompt format would need to verify
    Gate B holds before flipping this test."""
    prompt_path = (
        PROJECT_ROOT / "skills" / "role_evaluator" / "prompts"
        / "stage2pre_prompt.txt"
    )
    text = prompt_path.read_text(encoding="utf-8")
    assert 'null | "NO_FUNCTIONAL_OVERLAP"' in text
    assert hasattr(_s2pre_module, "_try_template_echo")
    echo = _s2pre_module._try_template_echo(
        '{"verdict": "PROCEED", "skip_reason": null | '
        '"NO_FUNCTIONAL_OVERLAP" | "WRONG_SENIORITY"}'
    )
    assert echo == {"verdict": "PROCEED", "skip_reason": None}
