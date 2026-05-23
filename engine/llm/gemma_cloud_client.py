"""Thin wrapper around Google AI Studio API for Gemma 4 31B.

Handles: API key loading, rate limiting (15 RPM), usage logging,
error classification, and daily budget tracking.

Does NOT handle: planning, prioritization, retry queuing.
That's the CallManager's job.

Uses the modern google-genai SDK (`from google import genai`),
not the deprecated google-generativeai package.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

logger = logging.getLogger(__name__)

PACIFIC = ZoneInfo("America/Los_Angeles")


class GemmaCloudError(Exception):
    """Base error for cloud eval failures."""


class RateLimitedError(GemmaCloudError):
    """429 from Google — caller may retry with backoff."""


class ServerError(GemmaCloudError):
    """500/503 from Google — call already counted, do NOT retry."""


class GemmaCloudClient:
    MODEL = "gemma-4-31b-it"
    RPM_LIMIT = 15
    RPM_SLEEP = 60.0 / RPM_LIMIT  # 4 seconds between calls
    SOFT_STOP = 1200              # used in usage-log budget_remaining

    def __init__(
        self,
        profile_id: str = "default",
        api_key: Optional[str] = None,
    ):
        self.api_key = api_key or self._load_api_key(profile_id)
        self._client = genai.Client(api_key=self.api_key)

        self.usage_log = Path(f"data/{profile_id}/cloud_eval_usage.jsonl")
        self.usage_log.parent.mkdir(parents=True, exist_ok=True)

        self._last_call_time = 0.0

    @staticmethod
    def _load_api_key(profile_id: str) -> str:
        """Load API key from env var or file (env var wins)."""
        env_key = os.environ.get("GEMINI_API_KEY")
        if env_key:
            return env_key.strip()

        key_file = Path(f"data/{profile_id}/gemini_api_key.txt")
        if key_file.exists():
            return key_file.read_text(encoding="utf-8").strip()

        raise ValueError(
            f"No API key found. Set GEMINI_API_KEY env var or create "
            f"{key_file} with your Google AI Studio API key."
        )

    def evaluate_posting(
        self,
        opportunity_id: int,
        employer: str,
        title: str,
        location: str,
        posting_text: str,
        inventory_summary: str,
        prompt_template: str,
    ) -> dict:
        """Single-call evaluate. Returns parsed JSON dict.

        Throttles to RPM_LIMIT and logs every call to usage_log.
        Raises GemmaCloudError subclasses on failure (the call is
        still logged before the exception).
        """
        self._throttle()

        # Use .replace() not .format() — the prompt contains literal
        # JSON braces (e.g. {"verdict":...}) that .format() would
        # mis-interpret as field references.
        prompt = (
            prompt_template
            .replace("{inventory_summary}", inventory_summary)
            .replace("{title}", title)
            .replace("{employer}", employer)
            .replace("{location}", location)
            .replace("{posting_text}", posting_text)
        )

        start_ms = time.monotonic_ns() // 1_000_000
        input_tokens = 0
        output_tokens = 0

        try:
            response = self._client.models.generate_content(
                model=self.MODEL,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0,
                ),
            )
            elapsed_ms = (time.monotonic_ns() // 1_000_000) - start_ms

            usage = getattr(response, "usage_metadata", None)
            if usage:
                input_tokens = getattr(usage, "prompt_token_count", 0) or 0
                output_tokens = (
                    getattr(usage, "candidates_token_count", 0) or 0
                )

            raw = (response.text or "").strip()
            parsed = json.loads(raw)

        except genai_errors.ClientError as e:
            elapsed_ms = (time.monotonic_ns() // 1_000_000) - start_ms
            error_msg = str(e)
            if getattr(e, "code", None) == 429:
                self._log_call(
                    opportunity_id, employer, title,
                    input_tokens, output_tokens, elapsed_ms,
                    None, None, "rate_limited", error_msg,
                )
                raise RateLimitedError(error_msg) from e
            self._log_call(
                opportunity_id, employer, title,
                input_tokens, output_tokens, elapsed_ms,
                None, None, "error", error_msg,
            )
            raise GemmaCloudError(error_msg) from e

        except genai_errors.ServerError as e:
            elapsed_ms = (time.monotonic_ns() // 1_000_000) - start_ms
            error_msg = str(e)
            self._log_call(
                opportunity_id, employer, title,
                input_tokens, output_tokens, elapsed_ms,
                None, None, "server_error", error_msg,
            )
            raise ServerError(error_msg) from e

        except (json.JSONDecodeError, ValueError) as e:
            elapsed_ms = (time.monotonic_ns() // 1_000_000) - start_ms
            error_msg = str(e)
            self._log_call(
                opportunity_id, employer, title,
                input_tokens, output_tokens, elapsed_ms,
                None, None, "parse_error", error_msg,
            )
            raise GemmaCloudError(error_msg) from e

        except Exception as e:
            elapsed_ms = (time.monotonic_ns() // 1_000_000) - start_ms
            error_msg = str(e)
            self._log_call(
                opportunity_id, employer, title,
                input_tokens, output_tokens, elapsed_ms,
                None, None, "error", error_msg,
            )
            raise GemmaCloudError(error_msg) from e

        verdict = parsed.get("verdict")
        scores = parsed.get("scores")

        if verdict not in ("PROCEED", "SKIP"):
            err = f"Invalid verdict: {verdict!r}"
            self._log_call(
                opportunity_id, employer, title,
                input_tokens, output_tokens, elapsed_ms,
                None, None, "parse_error", err,
            )
            raise GemmaCloudError(err)

        self._log_call(
            opportunity_id, employer, title,
            input_tokens, output_tokens, elapsed_ms,
            verdict, scores, "ok", None,
        )
        return parsed

    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.4,
    ) -> str:
        """Free-form text generation primitive (Phase 11.6 QA agent).

        Throttles to RPM_LIMIT but does NOT log to the JSONL usage
        file -- only evaluate_posting (the cost-bounded eval path)
        writes to the audit trail. Q&A calls are interactive and
        cached; logging would add noise.

        Returns the response text. Raises GemmaCloudError subclasses
        on failure for caller to catch.
        """
        self._throttle()
        contents = (
            f"{system}\n\n{prompt}" if system else prompt
        )
        try:
            response = self._client.models.generate_content(
                model=self.MODEL,
                contents=contents,
                config=genai_types.GenerateContentConfig(
                    temperature=temperature,
                ),
            )
        except genai_errors.ClientError as e:
            if getattr(e, "code", None) == 429:
                raise RateLimitedError(str(e)) from e
            raise GemmaCloudError(str(e)) from e
        except genai_errors.ServerError as e:
            raise ServerError(str(e)) from e
        except Exception as e:
            raise GemmaCloudError(str(e)) from e
        return (response.text or "").strip()

    def count_today(self) -> int:
        """Count API calls in the current AGENT-DAY.

        Anchors on engine.utils.day_boundary so the dashboard's
        "calls today" panel and SOFT_STOP enforcement always
        agree. Default boundary 3 AM Toronto = midnight Pacific
        = Google's free-tier quota reset.
        """
        if not self.usage_log.exists():
            return 0

        from engine.utils.day_boundary import (
            current_agent_day,
            entry_agent_day,
        )
        today = current_agent_day()
        count = 0
        with open(self.usage_log, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = entry.get("timestamp", "")
                if not ts:
                    continue
                try:
                    dt_utc = datetime.fromisoformat(
                        ts.replace("Z", "+00:00")
                    )
                except ValueError:
                    continue
                if entry_agent_day(dt_utc) == today:
                    count += 1
        return count

    def _throttle(self):
        """Enforce RPM limit by sleeping between calls."""
        now = time.monotonic()
        elapsed = now - self._last_call_time
        if elapsed < self.RPM_SLEEP:
            time.sleep(self.RPM_SLEEP - elapsed)
        self._last_call_time = time.monotonic()

    def _log_call(
        self, opportunity_id, employer, title,
        input_tokens, output_tokens, latency_ms,
        verdict, scores, status, error,
    ):
        """Append one line to usage log. Every call, no exceptions."""
        daily_count = self.count_today() + 1  # includes this call
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "opportunity_id": opportunity_id,
            "employer": employer,
            "title": title,
            "model": self.MODEL,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "latency_ms": latency_ms,
            "verdict": verdict,
            "scores": scores,
            "status": status,
            "daily_count": daily_count,
            "budget_remaining": self.SOFT_STOP - daily_count,
        }
        if error:
            entry["error"] = error[:500]

        with open(self.usage_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
