"""Unit tests for engine.applicant.qa_generator (no live LLM)."""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.applicant.qa_generator import (  # noqa: E402
    QAGenerator,
    _normalize_key,
)


class FakeClient:
    """Minimal stand-in for GemmaCloudClient."""

    SOFT_STOP = 1200

    def __init__(self, response: str = "Default draft answer.",
                 used_today: int = 0, raises: Exception | None = None):
        self.response = response
        self._used = used_today
        self.calls: list[dict] = []
        self._raises = raises

    def count_today(self) -> int:
        return self._used

    def generate(self, prompt: str, system: str | None = None,
                 temperature: float = 0.4) -> str:
        self.calls.append(
            {"prompt": prompt, "system": system,
             "temperature": temperature},
        )
        if self._raises is not None:
            raise self._raises
        return self.response


def test_normalize_key_collapses_whitespace_and_lowercases():
    a = _normalize_key("Are you legally  AUTHORIZED to work?")
    b = _normalize_key("are you legally authorized to work?")
    assert a == b


def test_propose_answer_cache_hit_returns_without_llm(tmp_path):
    cache = tmp_path / "cache.json"
    cache.write_text(
        '{"any prior question?": "Cached reply"}',
        encoding="utf-8",
    )
    g = QAGenerator(
        cloud_client=FakeClient(),
        inventory_summary="...",
        cache_path=cache, interactive=False,
    )
    r = g.propose_answer("Any prior question?")
    assert r.source == "cache"
    assert r.answer == "Cached reply"
    assert g.client.calls == []  # no LLM call


def test_propose_answer_cache_miss_calls_llm_but_does_not_persist(
    tmp_path,
):
    cache = tmp_path / "cache.json"
    g = QAGenerator(
        cloud_client=FakeClient(response="Generated reply."),
        inventory_summary="ten years of delivery experience",
        cache_path=cache, interactive=False,
    )
    r = g.propose_answer(
        "Tell me about a complex stakeholder.",
        posting={"title": "PM", "employer": "Acme"},
    )
    assert r.source == "generated"
    assert r.answer == "Generated reply."
    assert len(g.client.calls) == 1
    assert "ten years of delivery experience" in g.client.calls[0]["prompt"]
    # Draft should NOT be in the cache yet -- caller decides.
    assert not cache.exists()


def test_confirm_answer_writes_cache_for_future_hits(tmp_path):
    cache = tmp_path / "cache.json"
    g = QAGenerator(
        cloud_client=FakeClient(response="Drafted."),
        inventory_summary="...", cache_path=cache, interactive=False,
    )
    g.confirm_answer("How do you handle conflict?", "Calm + curious.")
    # New instance reading the same file should see the cached entry.
    g2 = QAGenerator(
        cloud_client=FakeClient(),
        inventory_summary="...", cache_path=cache, interactive=False,
    )
    r = g2.propose_answer("How do you handle conflict?")
    assert r.source == "cache"
    assert r.answer == "Calm + curious."
    assert g2.client.calls == []


def test_confirm_answer_collapses_to_canonical_key(tmp_path):
    cache = tmp_path / "cache.json"
    g = QAGenerator(
        cloud_client=FakeClient(),
        inventory_summary="...", cache_path=cache, interactive=False,
    )
    g.confirm_answer(
        "Are you LEGALLY  authorized to work?",
        "Yes -- Permanent Resident.",
    )
    r = g.propose_answer("are you legally authorized to work?")
    assert r.source == "cache"


def test_propose_answer_budget_guard_skips(tmp_path):
    cache = tmp_path / "cache.json"
    g = QAGenerator(
        cloud_client=FakeClient(used_today=1500),
        inventory_summary="...", cache_path=cache, interactive=False,
    )
    r = g.propose_answer("Anything new?")
    assert r.source == "skipped"
    assert r.answer is None
    assert g.client.calls == []


def test_propose_answer_handles_llm_failure_gracefully(tmp_path):
    cache = tmp_path / "cache.json"
    g = QAGenerator(
        cloud_client=FakeClient(raises=RuntimeError("network down")),
        inventory_summary="...", cache_path=cache, interactive=False,
    )
    r = g.propose_answer("Why this role?")
    assert r.source == "skipped"
    assert r.answer is None


def test_confirm_answer_ignores_blank(tmp_path):
    cache = tmp_path / "cache.json"
    g = QAGenerator(
        cloud_client=FakeClient(),
        inventory_summary="...", cache_path=cache, interactive=False,
    )
    g.confirm_answer("Anything?", "")
    assert not cache.exists()
