"""Unit tests for LLMClient.generate_chat() and chat().

Mocks requests.post; no live Ollama needed.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from llm.client import LLMClient  # noqa: E402


def _fake_ollama_response(content: str = "ok") -> dict:
    """Shape that matches Ollama /api/chat non-streaming response."""
    return {
        "model": "gemma4:e4b",
        "created_at": "2026-05-03T00:00:00Z",
        "message": {"role": "assistant", "content": content},
        "done": True,
        "prompt_eval_count": 100,
        "prompt_eval_duration": 5_000_000_000,  # 5s in ns
        "eval_count": 50,
        "eval_duration": 3_000_000_000,
        "total_duration": 8_000_000_000,
    }


def _make_mock_post(content: str = "ok") -> MagicMock:
    response = MagicMock()
    response.json.return_value = _fake_ollama_response(content)
    response.raise_for_status.return_value = None
    mock_post = MagicMock(return_value=response)
    return mock_post


def _payload_from(mock_post: MagicMock) -> dict:
    """Pull the JSON payload out of the requests.post call args."""
    assert mock_post.call_count == 1
    _, kwargs = mock_post.call_args
    return kwargs["json"]


# --- Message structure ------------------------------------------------

def test_generate_chat_sends_system_and_user_messages():
    mock_post = _make_mock_post()
    with patch("llm.client.requests.post", mock_post):
        client = LLMClient(model="gemma4:e4b")
        client.generate_chat(
            system_prompt="STATIC PREFIX",
            user_prompt="DYNAMIC SUFFIX",
        )
    payload = _payload_from(mock_post)
    assert payload["messages"] == [
        {"role": "system", "content": "STATIC PREFIX"},
        {"role": "user", "content": "DYNAMIC SUFFIX"},
    ]


def test_generate_chat_targets_api_chat_endpoint():
    mock_post = _make_mock_post()
    with patch("llm.client.requests.post", mock_post):
        client = LLMClient(model="gemma4:e4b", host="http://h:1")
        client.generate_chat("s", "u")
    args, _ = mock_post.call_args
    assert args[0] == "http://h:1/api/chat"


# --- Options forwarding -----------------------------------------------

def test_generate_chat_forwards_temperature():
    mock_post = _make_mock_post()
    with patch("llm.client.requests.post", mock_post):
        client = LLMClient(model="gemma4:e4b")
        client.generate_chat("s", "u", temperature=0.7)
    payload = _payload_from(mock_post)
    assert payload["options"]["temperature"] == 0.7


def test_generate_chat_forwards_num_predict():
    mock_post = _make_mock_post()
    with patch("llm.client.requests.post", mock_post):
        client = LLMClient(model="gemma4:e4b")
        client.generate_chat("s", "u", num_predict=200)
    payload = _payload_from(mock_post)
    assert payload["options"]["num_predict"] == 200


def test_generate_chat_omits_num_predict_when_none():
    mock_post = _make_mock_post()
    with patch("llm.client.requests.post", mock_post):
        client = LLMClient(model="gemma4:e4b")
        client.generate_chat("s", "u")
    payload = _payload_from(mock_post)
    assert "num_predict" not in payload["options"]


def test_generate_chat_forwards_num_ctx():
    mock_post = _make_mock_post()
    with patch("llm.client.requests.post", mock_post):
        client = LLMClient(model="gemma4:e4b")
        client.generate_chat("s", "u", num_ctx=4096)
    payload = _payload_from(mock_post)
    assert payload["options"]["num_ctx"] == 4096


def test_generate_chat_omits_num_ctx_when_none():
    mock_post = _make_mock_post()
    with patch("llm.client.requests.post", mock_post):
        client = LLMClient(model="gemma4:e4b")
        client.generate_chat("s", "u")
    payload = _payload_from(mock_post)
    assert "num_ctx" not in payload["options"]


def test_generate_chat_sets_keep_alive_minus_one_by_default():
    """Ollama parses keep_alive as a Go duration. The string '-1'
    fails with 'missing unit in duration'; the integer -1 is the
    correct sentinel meaning 'keep loaded forever'."""
    mock_post = _make_mock_post()
    with patch("llm.client.requests.post", mock_post):
        client = LLMClient(model="gemma4:e4b")
        client.generate_chat("s", "u")
    payload = _payload_from(mock_post)
    assert payload["keep_alive"] == -1


def test_generate_chat_keep_alive_override_int():
    mock_post = _make_mock_post()
    with patch("llm.client.requests.post", mock_post):
        client = LLMClient(model="gemma4:e4b")
        client.generate_chat("s", "u", keep_alive=0)
    payload = _payload_from(mock_post)
    assert payload["keep_alive"] == 0


def test_generate_chat_keep_alive_override_duration_string():
    mock_post = _make_mock_post()
    with patch("llm.client.requests.post", mock_post):
        client = LLMClient(model="gemma4:e4b")
        client.generate_chat("s", "u", keep_alive="24h")
    payload = _payload_from(mock_post)
    assert payload["keep_alive"] == "24h"


# --- Model override ---------------------------------------------------

def test_generate_chat_uses_model_override():
    mock_post = _make_mock_post()
    with patch("llm.client.requests.post", mock_post):
        client = LLMClient(model="gemma4:e4b")
        client.generate_chat(
            "s", "u", model="gemma3-4b-ctx4k",
        )
    payload = _payload_from(mock_post)
    assert payload["model"] == "gemma3-4b-ctx4k"


def test_generate_chat_uses_self_model_when_no_override():
    mock_post = _make_mock_post()
    with patch("llm.client.requests.post", mock_post):
        client = LLMClient(model="gemma4:e4b")
        client.generate_chat("s", "u")
    payload = _payload_from(mock_post)
    assert payload["model"] == "gemma4:e4b"


# --- Response shaping -------------------------------------------------

def test_generate_chat_extracts_response_text():
    mock_post = _make_mock_post(content="hello world")
    with patch("llm.client.requests.post", mock_post):
        client = LLMClient(model="gemma4:e4b")
        data = client.generate_chat("s", "u")
    assert data["response"] == "hello world"
    # Original message structure preserved as well.
    assert data["message"]["content"] == "hello world"


def test_generate_chat_returns_full_timing_dict():
    mock_post = _make_mock_post()
    with patch("llm.client.requests.post", mock_post):
        client = LLMClient(model="gemma4:e4b")
        data = client.generate_chat("s", "u")
    assert data["prompt_eval_duration"] == 5_000_000_000
    assert data["eval_duration"] == 3_000_000_000
    assert data["total_duration"] == 8_000_000_000


# --- chat() convenience wrapper ---------------------------------------

def test_generate_chat_forwards_think_false():
    """Top-level think=False suppresses reasoning trace on
    thinking-capable models (gemma4:e4b)."""
    mock_post = _make_mock_post()
    with patch("llm.client.requests.post", mock_post):
        client = LLMClient(model="gemma4:e4b")
        client.generate_chat("s", "u", think=False)
    payload = _payload_from(mock_post)
    assert payload["think"] is False


def test_generate_chat_forwards_think_true():
    mock_post = _make_mock_post()
    with patch("llm.client.requests.post", mock_post):
        client = LLMClient(model="gemma4:e4b")
        client.generate_chat("s", "u", think=True)
    payload = _payload_from(mock_post)
    assert payload["think"] is True


def test_generate_chat_omits_think_when_none():
    """No `think` key in payload when caller doesn't set it -
    preserves backward-compat for callers that don't care."""
    mock_post = _make_mock_post()
    with patch("llm.client.requests.post", mock_post):
        client = LLMClient(model="gemma4:e4b")
        client.generate_chat("s", "u")
    payload = _payload_from(mock_post)
    assert "think" not in payload


def test_chat_returns_string_only():
    mock_post = _make_mock_post(content="terse reply")
    with patch("llm.client.requests.post", mock_post):
        client = LLMClient(model="gemma4:e4b")
        out = client.chat("s", "u")
    assert isinstance(out, str)
    assert out == "terse reply"


def test_chat_forwards_kwargs_to_generate_chat():
    mock_post = _make_mock_post()
    with patch("llm.client.requests.post", mock_post):
        client = LLMClient(model="gemma4:e4b")
        client.chat(
            "s", "u",
            temperature=0.0, num_predict=42, num_ctx=8192,
        )
    payload = _payload_from(mock_post)
    assert payload["options"]["temperature"] == 0.0
    assert payload["options"]["num_predict"] == 42
    assert payload["options"]["num_ctx"] == 8192
