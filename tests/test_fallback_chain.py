"""Spec 12 TASK 1 — fallback-chain tests."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from engine.llm.fallback_chain import (
    AuthError, FallbackChain, FallbackPriority, OllamaUnreachableError,
    RateLimitError, TimeoutError,
    event_to_history,
)


def _noop_sleep(_: float) -> None:
    return None


def test_first_priority_succeeds_no_fallback():
    chain = FallbackChain(
        "scorer",
        [FallbackPriority("api", lambda p: "ok-api"),
         FallbackPriority("local", lambda p: "ok-local")],
        sleep=_noop_sleep,
    )
    r = chain.execute("hello")
    assert r.success is True
    assert r.provider == "api"
    assert r.fallbacks_used == 0
    assert r.events[-1].outcome == "ok"


def test_auth_error_falls_through_without_retry():
    calls = []

    def bad(p):
        calls.append("api")
        raise AuthError("invalid key")

    def good(p):
        calls.append("local")
        return "ok"

    chain = FallbackChain(
        "x",
        [FallbackPriority("api", bad, retries=5),
         FallbackPriority("local", good)],
        sleep=_noop_sleep,
    )
    r = chain.execute("p")
    assert r.success is True
    assert r.provider == "local"
    assert r.fallbacks_used == 1
    # Auth errors skip retries — bad() called exactly once.
    assert calls.count("api") == 1


def test_ollama_unreachable_falls_through_without_retry():
    calls = []

    def gone(p):
        calls.append(1)
        raise OllamaUnreachableError("daemon down")

    chain = FallbackChain(
        "x",
        [FallbackPriority("local", gone, retries=5),
         FallbackPriority("api", lambda p: "ok")],
        sleep=_noop_sleep,
    )
    r = chain.execute("p")
    assert r.success is True
    assert len(calls) == 1


def test_rate_limit_retries_then_falls():
    state = {"calls": 0}

    def flaky(p):
        state["calls"] += 1
        raise RateLimitError("429")

    def fallback(p):
        return "ok-fallback"

    chain = FallbackChain(
        "x",
        [FallbackPriority("api", flaky, retries=3),
         FallbackPriority("local", fallback)],
        sleep=_noop_sleep,
    )
    r = chain.execute("p")
    # Should have retried 3 times (4 calls total) then fallen.
    assert state["calls"] == 4
    assert r.provider == "local"
    assert any(e.outcome == "exhausted" for e in r.events)


def test_timeout_retries_with_backoff():
    state = {"calls": 0}
    sleeps: list[float] = []

    def slow(p):
        state["calls"] += 1
        if state["calls"] < 3:
            raise TimeoutError("slow")
        return "ok"

    chain = FallbackChain(
        "x",
        [FallbackPriority("api", slow, retries=5, backoff_seconds=0.5)],
        sleep=sleeps.append,
    )
    r = chain.execute("p")
    assert r.success is True
    # 2 retries → 2 sleeps. Backoff = 0.5 * 2^(attempt-1).
    assert sleeps == [0.5, 1.0]


def test_unbreakable_fallback_when_everything_exhausts():
    chain = FallbackChain(
        "x",
        [FallbackPriority("api", lambda p: (_ for _ in ()).throw(
            RateLimitError("rl")), retries=0)],
        unbreakable_value="MANUAL_FALLBACK_VALUE",
        sleep=_noop_sleep,
    )
    r = chain.execute("p")
    assert r.success is False
    assert r.output == "MANUAL_FALLBACK_VALUE"


def test_unbreakable_defaults_to_prompt_when_unset():
    chain = FallbackChain(
        "x",
        [FallbackPriority("api", lambda p: (_ for _ in ()).throw(
            AuthError("bad")), retries=0)],
        sleep=_noop_sleep,
    )
    r = chain.execute("the prompt")
    assert r.success is False
    assert r.output == "the prompt"


def test_unclassified_exception_falls_through():
    chain = FallbackChain(
        "x",
        [FallbackPriority("api", lambda p: (_ for _ in ()).throw(
            ValueError("weird"))),
         FallbackPriority("local", lambda p: "ok")],
        sleep=_noop_sleep,
    )
    r = chain.execute("p")
    assert r.success is True
    assert r.provider == "local"


def test_event_to_history_serialises_events():
    chain = FallbackChain(
        "scorer",
        [FallbackPriority("api", lambda p: "ok")],
        sleep=_noop_sleep,
    )
    r = chain.execute("p")
    h = event_to_history("scorer", r, "2026-05-19T00:00:00+00:00")
    assert h.function_name == "scorer"
    assert h.success is True
    assert h.events[0]["outcome"] == "ok"
