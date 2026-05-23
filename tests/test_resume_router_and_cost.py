"""Spec 8 — prompt_generator + router + cost_estimator tests."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from engine.resume import cost_estimator, prompt_generator, router


# --- prompt_generator -------------------------------------------

def test_generate_returns_non_empty_prompt():
    inputs = prompt_generator.ResumePromptInputs(
        posting={"title": "PM", "employer": "Acme",
                 "posting_text": "Hi"},
        inventory_text="some inventory",
    )
    out = prompt_generator.generate(inputs)
    assert out
    assert "PM" in out


def test_generate_prepends_scorer_header_when_provided():
    inputs = prompt_generator.ResumePromptInputs(
        posting={"title": "PM", "employer": "Acme",
                 "posting_text": "Hi"},
        scorer_output={
            "matched": ["Agile", "Jira"],
            "missed": ["Kubernetes"],
            "bridge_note": "Adjacent: cloud ops",
        },
    )
    out = prompt_generator.generate(inputs)
    assert "SCORER CONTEXT" in out
    assert "Agile" in out
    assert "Kubernetes" in out
    assert "Adjacent: cloud ops" in out


def test_generate_skips_header_when_no_scorer_output():
    inputs = prompt_generator.ResumePromptInputs(
        posting={"title": "PM", "employer": "Acme",
                 "posting_text": "Hi"},
    )
    out = prompt_generator.generate(inputs)
    assert "SCORER CONTEXT" not in out


def test_estimate_token_count_returns_positive():
    assert prompt_generator.estimate_token_count("hello world") >= 1


# --- router -----------------------------------------------------

def test_router_copy_paste_returns_prompt_verbatim():
    r = router.route(
        "the prompt",
        routing_config={"provider": "copy_paste"},
    )
    assert r.path == "copy_paste"
    assert r.output_text == "the prompt"
    assert r.warnings == []


def test_router_api_path_invokes_caller():
    api_caller = MagicMock(return_value="resp text")
    r = router.route(
        "the prompt",
        routing_config={"provider": "openai", "model": "gpt-4o"},
        api_caller=api_caller,
    )
    assert r.path == "api"
    assert r.provider == "openai"
    assert r.model == "gpt-4o"
    assert r.output_text == "resp text"
    api_caller.assert_called_once_with("openai", "gpt-4o", "the prompt")


def test_router_api_failure_falls_back_to_prompt():
    api_caller = MagicMock(side_effect=RuntimeError("rate-limit"))
    r = router.route(
        "the prompt",
        routing_config={"provider": "anthropic", "model": "claude"},
        api_caller=api_caller,
    )
    assert r.output_text == "the prompt"
    assert any("rate-limit" in w for w in r.warnings)


def test_router_local_invokes_ollama_caller():
    caller = MagicMock(return_value="ollama resp")
    r = router.route(
        "the prompt",
        routing_config={"provider": "local", "model": "gemma2:9b"},
        ollama_caller=caller,
    )
    assert r.path == "local"
    assert r.output_text == "ollama resp"
    caller.assert_called_once_with("gemma2:9b", "the prompt")


def test_router_local_flags_small_model_warning():
    caller = MagicMock(return_value="ok")
    r = router.route(
        "the prompt",
        routing_config={"provider": "local", "model": "gemma2:2b"},
        ollama_caller=caller,
    )
    assert any("≤4B" in w for w in r.warnings)


def test_router_local_no_model_warns():
    caller = MagicMock()
    r = router.route(
        "p",
        routing_config={"provider": "local", "model": ""},
        ollama_caller=caller,
    )
    assert any("No local model" in w for w in r.warnings)
    caller.assert_not_called()


def test_is_small_model_detects_1b_4b():
    assert router.is_small_model("gemma2:2b")
    assert router.is_small_model("phi3:3.8b")
    assert router.is_small_model("llama3.2:1b")
    assert router.is_small_model("qwen2:4b")
    assert not router.is_small_model("gemma2:9b")
    assert not router.is_small_model("llama3.1:70b")


# --- cost_estimator ---------------------------------------------

def test_per_app_cost_known_provider():
    # Sonnet: 3K input * $3/M + 1K output * $15/M = $0.009 + $0.015 = $0.024.
    assert cost_estimator.per_app_cost("anthropic_sonnet") == pytest.approx(0.024)


def test_per_app_cost_local_is_free():
    assert cost_estimator.per_app_cost("local") == 0.0


def test_per_app_cost_unknown_raises():
    with pytest.raises(ValueError):
        cost_estimator.per_app_cost("nope")


def test_estimate_scales_by_apps_per_week():
    weekly = cost_estimator.estimate("openai_gpt4o_mini", apps_per_week=10)
    monthly = cost_estimator.estimate("openai_gpt4o_mini", apps_per_week=20)
    assert monthly.monthly_usd > weekly.monthly_usd


def test_estimate_all_returns_one_per_provider():
    rows = cost_estimator.estimate_all(apps_per_week=5)
    assert len(rows) == len(cost_estimator.RATES)
    assert {r.provider for r in rows} == set(cost_estimator.RATES.keys())
