"""Tests for engine.matching.gemma_scorer.

All tests mock requests.post so no live Ollama is needed."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import requests

from engine.matching.gemma_scorer import (
    build_prompt,
    call_gemma,
    validate_gemma_response,
)


# ----- build_prompt -------------------------------------------------------

def test_build_prompt_includes_all_sections():
    p = build_prompt(
        posting_title="PM",
        posting_employer="Acme",
        posting_labels=["Java", "Scrum"],
        inventory_labels=["Java", "Agile"],
        overlap_labels=["Java"],
    )
    assert "POSTING SKILLS" in p
    assert "CANDIDATE SKILLS" in p
    assert "DIRECT OVERLAPS" in p
    assert "Score the overall fit from 1-10" in p
    assert "transferable skills" in p
    assert "critical gaps" in p


def test_build_prompt_includes_overlap_labels():
    p = build_prompt(
        posting_title="x", posting_employer="y",
        posting_labels=["a", "b"],
        inventory_labels=["a"],
        overlap_labels=["a"],
    )
    # Overlap label appears in JSON-serialized form.
    assert '"a"' in p


# ----- validate_gemma_response --------------------------------------------

def test_validate_gemma_response_zero_flags_on_clean():
    parsed = {
        "top_matches": [
            {"posting_skill": "Java", "reason": "..."},
            {"posting_skill": "Scrum", "reason": "..."},
        ],
    }
    overlap = {"Java", "Scrum", "Agile"}
    assert validate_gemma_response(parsed, overlap) == 0


def test_validate_gemma_response_flags_hallucinated_match():
    parsed = {
        "top_matches": [
            {"posting_skill": "Java", "reason": "..."},
            {"posting_skill": "Rust", "reason": "..."},  # not in overlap
        ],
    }
    overlap = {"Java", "Scrum"}
    assert validate_gemma_response(parsed, overlap) == 1


def test_validate_gemma_response_empty_matches():
    assert validate_gemma_response({}, set()) == 0
    assert validate_gemma_response({"top_matches": []}, set()) == 0


# ----- call_gemma (mocked) ------------------------------------------------

def _fake_response(response_text: str, status: int = 200):
    m = MagicMock()
    m.raise_for_status = MagicMock()
    m.json = MagicMock(return_value={"response": response_text})
    if status >= 400:
        m.raise_for_status.side_effect = requests.HTTPError(
            f"{status} error"
        )
    return m


def test_call_gemma_parses_clean_json():
    body = json.dumps({"score": 7, "summary": "ok"})
    with patch.object(
        requests, "post", return_value=_fake_response(body),
    ):
        parsed, raw = call_gemma("any prompt")
    assert parsed == {"score": 7, "summary": "ok"}
    assert raw == body


def test_call_gemma_strips_markdown_fences():
    fenced = (
        "```json\n"
        + json.dumps({"score": 5, "summary": "fine"})
        + "\n```"
    )
    with patch.object(
        requests, "post", return_value=_fake_response(fenced),
    ):
        parsed, raw = call_gemma("any prompt")
    assert parsed == {"score": 5, "summary": "fine"}
    # raw preserves the original (with fences).
    assert raw == fenced


def test_call_gemma_raises_on_malformed():
    with patch.object(
        requests, "post",
        return_value=_fake_response("this is not json"),
    ):
        with pytest.raises(ValueError):
            call_gemma("any prompt")
