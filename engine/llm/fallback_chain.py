"""Spec 12 TASK 1 — fallback chain engine.

A :class:`FallbackChain` walks a priority list of LLM endpoints
(each a callable that takes a prompt and returns a string),
classifies the error each one raises, and falls through to the
next according to the classification rules in Spec 12.

Each priority is wrapped as a :class:`FallbackPriority` so we can
attach a stable name (for the result + history log) and a per-
priority retry policy without subclassing.

The router from Spec 8 (engine/resume/router.py) already does a
tiny try-fall-back-to-copy-paste version of this; Spec 12's
chain is the general form: any function on any subsystem
(scorer, resume, form-filler) can wrap itself in a chain and
get consistent retry semantics for free.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable


class RateLimitError(Exception):
    """429 / quota-exceeded from an API provider."""


class AuthError(Exception):
    """401 / invalid-key from an API provider."""


class TimeoutError(Exception):  # noqa: A001 — shadowing builtin intentionally
    """Provider didn't respond in time."""


class OllamaUnreachableError(Exception):
    """Local Ollama daemon down / model missing."""


# Error classes the chain can react to. Anything else is raised
# straight through; the chain only handles known-bad provider
# states.
_RETRYABLE = (RateLimitError, TimeoutError)
_IMMEDIATE_FALL = (AuthError, OllamaUnreachableError)


@dataclass(frozen=True)
class FallbackPriority:
    name: str
    callable: Callable[[str], str]
    retries: int = 3            # max retries on RateLimit / Timeout
    backoff_seconds: float = 1.0


@dataclass(frozen=True)
class FallbackEvent:
    priority: str
    outcome: str                # "ok" | "retried" | "fell-through" | "exhausted"
    error: str | None = None
    attempt: int = 1


@dataclass(frozen=True)
class FallbackResult:
    success: bool
    output: str
    provider: str | None
    fallbacks_used: int          # count of priorities tried before success
    events: list[FallbackEvent]


class FallbackChain:
    def __init__(
        self,
        function_name: str,
        priorities: list[FallbackPriority],
        *,
        unbreakable_value: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.function_name = function_name
        self.priorities = priorities
        # Last-resort value the chain falls back to once every
        # priority is exhausted. For LLM tasks this is usually the
        # raw prompt (so the user can paste it manually). For
        # scoring it might be a neutral score; the caller decides.
        self.unbreakable_value = unbreakable_value
        self._sleep = sleep

    def execute(self, prompt: str, **kwargs: Any) -> FallbackResult:
        events: list[FallbackEvent] = []
        used = 0
        for priority in self.priorities:
            attempt = 0
            while True:
                attempt += 1
                try:
                    result = priority.callable(prompt, **kwargs)
                except _IMMEDIATE_FALL as e:
                    events.append(FallbackEvent(
                        priority=priority.name,
                        outcome="fell-through",
                        error=f"{type(e).__name__}: {e}",
                        attempt=attempt,
                    ))
                    used += 1
                    break
                except _RETRYABLE as e:
                    if attempt > priority.retries:
                        events.append(FallbackEvent(
                            priority=priority.name,
                            outcome="exhausted",
                            error=f"{type(e).__name__}: {e}",
                            attempt=attempt,
                        ))
                        used += 1
                        break
                    events.append(FallbackEvent(
                        priority=priority.name,
                        outcome="retried",
                        error=f"{type(e).__name__}: {e}",
                        attempt=attempt,
                    ))
                    self._sleep(
                        priority.backoff_seconds * (2 ** (attempt - 1)),
                    )
                    continue
                except Exception as e:
                    # Unclassified failure — log + fall through, don't
                    # raise. Better to deliver the user SOMETHING (even
                    # the unbreakable fallback) than crash the route.
                    events.append(FallbackEvent(
                        priority=priority.name,
                        outcome="fell-through",
                        error=f"{type(e).__name__}: {e}",
                        attempt=attempt,
                    ))
                    used += 1
                    break
                else:
                    events.append(FallbackEvent(
                        priority=priority.name, outcome="ok",
                        attempt=attempt,
                    ))
                    return FallbackResult(
                        success=True,
                        output=result,
                        provider=priority.name,
                        fallbacks_used=used,
                        events=events,
                    )

        # Every priority exhausted — return the unbreakable fallback.
        return FallbackResult(
            success=False,
            output=self.unbreakable_value if self.unbreakable_value
                    is not None else prompt,
            provider=None,
            fallbacks_used=used,
            events=events,
        )


# --- Event-history persistence (TASK 3) -------------------------

@dataclass
class FallbackHistoryEntry:
    timestamp: str
    function_name: str
    success: bool
    provider: str | None
    fallbacks_used: int
    events: list[dict] = field(default_factory=list)


def event_to_history(
    function_name: str, result: FallbackResult, timestamp: str,
) -> FallbackHistoryEntry:
    return FallbackHistoryEntry(
        timestamp=timestamp,
        function_name=function_name,
        success=result.success,
        provider=result.provider,
        fallbacks_used=result.fallbacks_used,
        events=[
            {
                "priority": e.priority,
                "outcome": e.outcome,
                "error": e.error,
                "attempt": e.attempt,
            }
            for e in result.events
        ],
    )
