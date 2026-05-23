"""Unit tests for engine.applicant.qa_generator."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.applicant.qa_generator import (  # noqa: E402
    QAGenerator,
    QAGeneratorResult,
    _normalize_key,
)


def _client(generated="Drafted answer.", count=10, soft=1200):
    c = MagicMock()
    c.generate = MagicMock(return_value=generated)
    c.count_today = MagicMock(return_value=count)
    c.SOFT_STOP = soft
    return c


def _gen(tmp_path, **kw):
    cache = tmp_path / "cache.json"
    return QAGenerator(
        cloud_client=kw.pop("cloud_client", _client()),
        inventory_summary="INV",
        cache_path=cache,
        interactive=kw.pop("interactive", True),
    )


def test_returns_cached_answer_without_calling_llm(tmp_path):
    g = _gen(tmp_path)
    g._cache = {_normalize_key("Why us?"): "Cached answer."}
    res = g.answer("Why us?")
    assert res.source == "cache"
    assert res.answer == "Cached answer."
    g.client.generate.assert_not_called()


def test_calls_llm_when_not_cached(tmp_path, monkeypatch):
    g = _gen(tmp_path)
    monkeypatch.setattr("builtins.input", lambda *a, **kw: "a")
    res = g.answer("Tell me about leadership.", posting={
        "title": "PM", "employer": "Acme",
    })
    assert res.source == "generated"
    assert res.answer == "Drafted answer."
    g.client.generate.assert_called_once()


def test_caches_after_accepted_draft(tmp_path, monkeypatch):
    g = _gen(tmp_path)
    monkeypatch.setattr("builtins.input", lambda *a, **kw: "a")
    g.answer("Why this role?")
    saved = json.loads(g.cache_path.read_text(encoding="utf-8"))
    assert saved[_normalize_key("Why this role?")] == "Drafted answer."


def test_returns_edited_answer_when_user_edits(tmp_path, monkeypatch):
    g = _gen(tmp_path)
    inputs = iter(["e", "My own answer."])
    monkeypatch.setattr(
        "builtins.input", lambda *a, **kw: next(inputs),
    )
    res = g.answer("Why this role?")
    assert res.source == "edited"
    assert res.answer == "My own answer."
    saved = json.loads(g.cache_path.read_text(encoding="utf-8"))
    assert saved[_normalize_key("Why this role?")] == "My own answer."


def test_returns_none_when_user_skips(tmp_path, monkeypatch):
    g = _gen(tmp_path)
    monkeypatch.setattr("builtins.input", lambda *a, **kw: "s")
    res = g.answer("Why this role?")
    assert res.source == "skipped"
    assert res.answer is None


def test_returns_none_when_budget_exhausted(tmp_path, capsys):
    g = _gen(
        tmp_path,
        cloud_client=_client(count=1200, soft=1200),
    )
    res = g.answer("Why this role?")
    assert res.source == "skipped"
    assert "soft_stop" in capsys.readouterr().out.lower()


def test_returns_none_on_llm_error(tmp_path, monkeypatch):
    bad = _client()
    bad.generate.side_effect = RuntimeError("boom")
    g = _gen(tmp_path, cloud_client=bad)
    res = g.answer("Why this role?")
    assert res.source == "skipped"
    assert res.answer is None


def test_normalizes_question_text_for_cache_key():
    a = _normalize_key("  Why  THIS role?  ")
    b = _normalize_key("why this role?")
    assert a == b


def test_non_interactive_mode_returns_skipped_when_uncached(tmp_path):
    g = _gen(tmp_path, interactive=False)
    res = g.answer("Why this role?")
    assert res.source == "skipped"
    g.client.generate.assert_not_called()
