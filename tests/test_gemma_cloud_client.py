"""Unit tests for engine.llm.gemma_cloud_client.

Mocks the google-genai SDK at the Client boundary — no live API.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.llm import gemma_cloud_client as mod  # noqa: E402
from engine.llm.gemma_cloud_client import (  # noqa: E402
    GemmaCloudClient,
    GemmaCloudError,
    RateLimitedError,
    ServerError,
)

PROMPT_TEMPLATE = (
    "INV={inventory_summary} "
    "T={title} E={employer} L={location} P={posting_text}"
)


# --- helpers -------------------------------------------------------

def _fake_response(text: str, prompt_tokens=100, output_tokens=20):
    """Mimic a google-genai GenerateContentResponse."""
    resp = MagicMock()
    resp.text = text
    usage = MagicMock()
    usage.prompt_token_count = prompt_tokens
    usage.candidates_token_count = output_tokens
    resp.usage_metadata = usage
    return resp


def _client_error(code: int, message: str = "rate limited"):
    """Build a real ClientError (or ServerError) with the given code."""
    from google.genai import errors
    cls = errors.ServerError if code >= 500 else errors.ClientError
    err = cls.__new__(cls)
    err.code = code
    err.status = "RESOURCE_EXHAUSTED" if code == 429 else "ERROR"
    err.message = message
    err.details = None
    err.response = None
    Exception.__init__(err, f"{code} {err.status}. {message}")
    return err


def _patched_client(monkeypatch, tmp_path, **client_kwargs):
    """Build a GemmaCloudClient with the genai SDK fully mocked.

    Returns (client, mock_models) so tests can wire generate_content.
    """
    monkeypatch.chdir(tmp_path)
    mock_sdk_client = MagicMock()
    mock_models = MagicMock()
    mock_sdk_client.models = mock_models
    monkeypatch.setattr(
        mod.genai, "Client", MagicMock(return_value=mock_sdk_client),
    )
    client = GemmaCloudClient(profile_id="test", api_key="fake", **client_kwargs)
    return client, mock_models


def _read_log(client) -> list[dict]:
    with open(client.usage_log, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# --- API key loading ----------------------------------------------

def test_api_key_from_env_var(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "env-key-123")
    monkeypatch.setattr(mod.genai, "Client", MagicMock())
    c = GemmaCloudClient(profile_id="test")
    assert c.api_key == "env-key-123"


def test_api_key_from_file(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    key_dir = tmp_path / "data" / "test"
    key_dir.mkdir(parents=True)
    (key_dir / "gemini_api_key.txt").write_text(
        "file-key-456\n", encoding="utf-8",
    )
    monkeypatch.setattr(mod.genai, "Client", MagicMock())
    c = GemmaCloudClient(profile_id="test")
    assert c.api_key == "file-key-456"


def test_api_key_missing_raises(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="No API key found"):
        GemmaCloudClient(profile_id="test")


# --- evaluate_posting happy paths ---------------------------------

def test_evaluate_returns_proceed(monkeypatch, tmp_path):
    client, models = _patched_client(monkeypatch, tmp_path)
    models.generate_content.return_value = _fake_response(
        '{"verdict":"PROCEED","skip_reason":null,'
        '"scores":{"function":3,"domain":2,"seniority":3,"disqualifier":false}}',
        prompt_tokens=2100, output_tokens=80,
    )
    result = client.evaluate_posting(
        opportunity_id=42, employer="Acme", title="Senior PM",
        location="Toronto", posting_text="Lead delivery.",
        inventory_summary="INV", prompt_template=PROMPT_TEMPLATE,
    )
    assert result["verdict"] == "PROCEED"
    assert result["scores"]["function"] == 3


def test_evaluate_returns_skip(monkeypatch, tmp_path):
    client, models = _patched_client(monkeypatch, tmp_path)
    models.generate_content.return_value = _fake_response(
        '{"verdict":"SKIP","skip_reason":"NO_FUNCTIONAL_OVERLAP",'
        '"scores":null}',
    )
    result = client.evaluate_posting(
        opportunity_id=43, employer="Bio Corp", title="Lab Tech",
        location="Toronto", posting_text="Pipette samples.",
        inventory_summary="INV", prompt_template=PROMPT_TEMPLATE,
    )
    assert result["verdict"] == "SKIP"
    assert result["skip_reason"] == "NO_FUNCTIONAL_OVERLAP"


# --- error classification -----------------------------------------

def test_429_raises_rate_limited(monkeypatch, tmp_path):
    client, models = _patched_client(monkeypatch, tmp_path)
    models.generate_content.side_effect = _client_error(429)
    with pytest.raises(RateLimitedError):
        client.evaluate_posting(
            opportunity_id=1, employer="A", title="B", location="C",
            posting_text="D", inventory_summary="E",
            prompt_template=PROMPT_TEMPLATE,
        )
    log = _read_log(client)
    assert log[-1]["status"] == "rate_limited"


def test_500_raises_server_error(monkeypatch, tmp_path):
    client, models = _patched_client(monkeypatch, tmp_path)
    models.generate_content.side_effect = _client_error(500, "internal")
    with pytest.raises(ServerError):
        client.evaluate_posting(
            opportunity_id=1, employer="A", title="B", location="C",
            posting_text="D", inventory_summary="E",
            prompt_template=PROMPT_TEMPLATE,
        )
    log = _read_log(client)
    assert log[-1]["status"] == "server_error"


def test_invalid_verdict_raises_parse_error(monkeypatch, tmp_path):
    client, models = _patched_client(monkeypatch, tmp_path)
    models.generate_content.return_value = _fake_response(
        '{"verdict":"MAYBE","scores":null}'
    )
    with pytest.raises(GemmaCloudError, match="Invalid verdict"):
        client.evaluate_posting(
            opportunity_id=1, employer="A", title="B", location="C",
            posting_text="D", inventory_summary="E",
            prompt_template=PROMPT_TEMPLATE,
        )
    log = _read_log(client)
    assert log[-1]["status"] == "parse_error"


def test_garbage_json_raises_parse_error(monkeypatch, tmp_path):
    client, models = _patched_client(monkeypatch, tmp_path)
    models.generate_content.return_value = _fake_response(
        "not json at all {",
    )
    with pytest.raises(GemmaCloudError):
        client.evaluate_posting(
            opportunity_id=1, employer="A", title="B", location="C",
            posting_text="D", inventory_summary="E",
            prompt_template=PROMPT_TEMPLATE,
        )
    log = _read_log(client)
    assert log[-1]["status"] == "parse_error"


# --- usage logging -------------------------------------------------

def test_usage_log_written_on_success(monkeypatch, tmp_path):
    client, models = _patched_client(monkeypatch, tmp_path)
    models.generate_content.return_value = _fake_response(
        '{"verdict":"PROCEED","skip_reason":null,'
        '"scores":{"function":3,"domain":2,"seniority":3,"disqualifier":false}}',
        prompt_tokens=2100, output_tokens=80,
    )
    client.evaluate_posting(
        opportunity_id=42, employer="Acme", title="Senior PM",
        location="Toronto", posting_text="X" * 5000,
        inventory_summary="INV", prompt_template=PROMPT_TEMPLATE,
    )
    log = _read_log(client)
    assert len(log) == 1
    e = log[0]
    assert e["opportunity_id"] == 42
    assert e["employer"] == "Acme"
    assert e["model"] == "gemma-4-31b-it"
    assert e["input_tokens"] == 2100
    assert e["output_tokens"] == 80
    assert e["verdict"] == "PROCEED"
    assert e["status"] == "ok"
    assert e["daily_count"] == 1
    assert e["budget_remaining"] == GemmaCloudClient.SOFT_STOP - 1


def test_usage_log_written_on_failure(monkeypatch, tmp_path):
    client, models = _patched_client(monkeypatch, tmp_path)
    models.generate_content.side_effect = _client_error(429)
    with pytest.raises(RateLimitedError):
        client.evaluate_posting(
            opportunity_id=99, employer="X", title="Y", location="Z",
            posting_text="W", inventory_summary="V",
            prompt_template=PROMPT_TEMPLATE,
        )
    log = _read_log(client)
    assert len(log) == 1
    assert log[0]["opportunity_id"] == 99
    assert log[0]["verdict"] is None
    assert log[0]["status"] == "rate_limited"
    assert "error" in log[0]


def test_posting_text_sent_in_full(monkeypatch, tmp_path):
    """TPM is unlimited on Gemma 4 31B — send the full posting."""
    client, models = _patched_client(monkeypatch, tmp_path)
    models.generate_content.return_value = _fake_response(
        '{"verdict":"SKIP","skip_reason":"OTHER","scores":null}'
    )
    client.evaluate_posting(
        opportunity_id=1, employer="A", title="B", location="C",
        posting_text="X" * 5000, inventory_summary="INV",
        prompt_template=PROMPT_TEMPLATE,
    )
    call = models.generate_content.call_args
    sent_prompt = call.kwargs["contents"]
    # All 5000 X's should be present — no truncation.
    assert "P=" + ("X" * 5000) in sent_prompt


# --- throttle -----------------------------------------------------

def test_rpm_throttle_sleeps(monkeypatch, tmp_path):
    client, models = _patched_client(monkeypatch, tmp_path)
    models.generate_content.return_value = _fake_response(
        '{"verdict":"SKIP","skip_reason":"OTHER","scores":null}'
    )
    sleeps = []
    monkeypatch.setattr(mod.time, "sleep", lambda s: sleeps.append(s))
    client.evaluate_posting(
        opportunity_id=1, employer="A", title="B", location="C",
        posting_text="D", inventory_summary="E",
        prompt_template=PROMPT_TEMPLATE,
    )
    client.evaluate_posting(
        opportunity_id=2, employer="A", title="B", location="C",
        posting_text="D", inventory_summary="E",
        prompt_template=PROMPT_TEMPLATE,
    )
    assert len(sleeps) >= 1
    assert sleeps[-1] > 0
    assert sleeps[-1] <= GemmaCloudClient.RPM_SLEEP + 0.01


# --- count_today --------------------------------------------------

def test_count_today_counts_pacific_date(monkeypatch, tmp_path):
    client, _ = _patched_client(monkeypatch, tmp_path)
    pacific_now = datetime.now(mod.PACIFIC)
    today_utc = pacific_now.astimezone(timezone.utc).isoformat()
    yesterday_utc = (
        pacific_now - timedelta(days=2)
    ).astimezone(timezone.utc).isoformat()
    with open(client.usage_log, "w", encoding="utf-8") as f:
        f.write(json.dumps({"timestamp": today_utc}) + "\n")
        f.write(json.dumps({"timestamp": today_utc}) + "\n")
        f.write(json.dumps({"timestamp": yesterday_utc}) + "\n")
    assert client.count_today() == 2


def test_count_today_zero_when_no_log(monkeypatch, tmp_path):
    client, _ = _patched_client(monkeypatch, tmp_path)
    if client.usage_log.exists():
        client.usage_log.unlink()
    assert client.count_today() == 0
