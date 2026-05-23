"""Generate resume / cover letter markdown via Google AI Studio.

Wraps GemmaCloudClient — the same google-genai client, API key, and
RPM throttle the evaluator already uses (model gemma-4-31b-it,
free tier 1,500 RPD). The evaluator's GemmaCloudClient.generate()
handles throttling (15 RPM) and error classification; this module
adds the resume-specific concern: logging each generation to
cloud_eval_usage.jsonl with a `call_type` field so the dashboard
can break daily usage down by purpose (evaluation vs. resume vs.
cover_letter).
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class CloudResumeGenerator:
    """Calls Google AI Studio to turn a structured prompt into resume
    or cover-letter markdown.

    `client` is injectable so tests can supply a fake without a real
    API key. `min_interval` adds an optional spacing floor on top of
    GemmaCloudClient's own 15-RPM throttle (used mainly so tests can
    assert spacing without a 4-second real throttle).
    """

    def __init__(
        self,
        profile_id: str = "default",
        client=None,
        min_interval: float = 0.0,
    ):
        self.profile_id = profile_id
        self._client = client
        self.min_interval = min_interval
        self._last_call = 0.0
        self.usage_log = (
            PROJECT_ROOT / "data" / profile_id / "cloud_eval_usage.jsonl"
        )

    def _ensure_client(self):
        if self._client is None:
            from engine.llm.gemma_cloud_client import GemmaCloudClient
            self._client = GemmaCloudClient(profile_id=self.profile_id)
        return self._client

    def _throttle(self) -> None:
        if self.min_interval <= 0:
            return
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call = time.monotonic()

    def generate(
        self,
        prompt: str,
        *,
        call_type: str = "resume",
        opportunity_id: Optional[int] = None,
        temperature: float = 0.7,
    ) -> str:
        """Send `prompt` to Gemma, return the generated markdown.

        Logs the call to cloud_eval_usage.jsonl tagged with
        `call_type` ("resume" / "cover_letter") so dashboard usage
        tracking can attribute it. A logging failure never masks a
        successful generation.
        """
        self._throttle()
        client = self._ensure_client()
        text = client.generate(prompt, temperature=temperature)
        self._log(call_type, opportunity_id, len(prompt), len(text or ""))
        return text or ""

    def _log(
        self, call_type: str, opportunity_id: Optional[int],
        prompt_chars: int, output_chars: int,
    ) -> None:
        """Append one usage entry via the shared cloud-budget counter.

        cloud_eval_usage.jsonl is shared with the evaluator: resume /
        cover-letter calls land in the same log the evaluator's
        count_today() reads, so generation automatically draws down
        the evaluator's available budget. Token counts are char/4
        estimates — the dashboard only needs call counts. Logging is
        best-effort: a failure never masks a successful generation.
        """
        try:
            from engine import cloud_budget
            cloud_budget.log_call(
                self.profile_id,
                call_type,
                path=self.usage_log,
                opportunity_id=opportunity_id,
                model="gemma-4-31b-it",
                input_tokens=prompt_chars // 4,
                output_tokens=output_chars // 4,
                status="ok",
            )
        except Exception as e:  # pragma: no cover — logging is best-effort
            logger.warning("cloud_generator usage log failed: %s", e)
