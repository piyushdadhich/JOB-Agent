"""Tier 2 screening-question generator.

Used by the application agent when QAMatcher (Tier 1) doesn't
match a question. Calls Gemma 4 31B for a draft answer, displays
it for user review (accept / edit / skip), caches the chosen
answer keyed by normalized question text.

Cache lives at config/applicant_qa_cache.json (gitignored).
Cache key normalization: lowercase + collapsed whitespace + trim
to 200 chars. Same screening question across companies reuses
the cached answer without burning another API call.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CACHE_PATH = (
    PROJECT_ROOT / "config" / "applicant_qa_cache.json"
)


SYSTEM_PROMPT = """\
You answer screening questions on job applications for a senior
delivery and operations professional with 10+ years of experience.

CRITICAL RULES:
1. Anti-fabrication: only reference experience present in the
   CANDIDATE INVENTORY below. Do NOT invent skills, roles, tools,
   metrics, or accomplishments.
2. First-person, conversational, professional tone. No bullet
   lists -- this fills a single textarea.
3. Length: 2-4 sentences (60-120 words typical).
4. Reference 1-2 concrete accomplishments from the inventory if
   they fit the question.
5. Output ONLY the answer text. No preamble, no explanation, no
   "Here is my answer:" framing.
"""


def _normalize_key(question: str) -> str:
    return " ".join((question or "").lower().split())[:200]


@dataclass(frozen=True)
class QAGeneratorResult:
    answer: Optional[str]
    source: str  # "cache" | "generated" | "edited" | "skipped"


class QAGenerator:
    """Tier 2 fallback: cloud Gemma + cache + interactive review."""

    def __init__(
        self,
        cloud_client,
        inventory_summary: str,
        cache_path: Optional[Path] = None,
        interactive: bool = True,
    ):
        self.client = cloud_client
        self.inventory_summary = inventory_summary
        self.cache_path = Path(cache_path or DEFAULT_CACHE_PATH)
        self.interactive = interactive
        self._cache = self._load_cache()

    def _load_cache(self) -> dict:
        if not self.cache_path.exists():
            return {}
        try:
            return json.loads(
                self.cache_path.read_text(encoding="utf-8"),
            )
        except Exception:
            return {}

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(self._cache, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def answer(
        self, question: str, posting: Optional[dict] = None,
    ) -> QAGeneratorResult:
        key = _normalize_key(question)
        if key in self._cache:
            return QAGeneratorResult(
                answer=self._cache[key], source="cache",
            )

        if not self.interactive:
            return QAGeneratorResult(answer=None, source="skipped")

        # Budget check.
        try:
            used = self.client.count_today()
            soft = getattr(self.client, "SOFT_STOP", 1200)
            if used >= soft:
                print(
                    f"  (Q skipped: cloud daily soft_stop hit "
                    f"{used}/{soft})"
                )
                return QAGeneratorResult(
                    answer=None, source="skipped",
                )
        except Exception:
            pass

        try:
            draft = self._call_llm(question, posting)
        except Exception as e:
            logger.warning("Gemma Q-answer failed: %s", e)
            return QAGeneratorResult(answer=None, source="skipped")

        print(f"\n  Q: {question[:200]}")
        print(f"  Draft answer: {draft}")
        try:
            choice = input(
                "  [a]ccept / [e]dit / [s]kip [a]: ",
            ).strip().lower() or "a"
        except (EOFError, KeyboardInterrupt):
            choice = "s"

        if choice == "s":
            return QAGeneratorResult(answer=None, source="skipped")

        if choice == "e":
            try:
                edited = input("  Your answer: ").strip()
            except (EOFError, KeyboardInterrupt):
                edited = ""
            if not edited:
                return QAGeneratorResult(
                    answer=None, source="skipped",
                )
            self._cache[key] = edited
            self._save_cache()
            return QAGeneratorResult(
                answer=edited, source="edited",
            )

        self._cache[key] = draft
        self._save_cache()
        return QAGeneratorResult(answer=draft, source="generated")

    def propose_answer(
        self, question: str, posting: Optional[dict] = None,
    ) -> QAGeneratorResult:
        """Cache-or-generate without prompting stdin.

        Used by the dashboard apply flow: surface a draft to the UI
        and let the user accept/edit/skip via HTTP. Cache write is
        deferred to confirm_answer() so a draft the user rejects
        doesn't pollute the cache.
        """
        key = _normalize_key(question)
        if key in self._cache:
            return QAGeneratorResult(
                answer=self._cache[key], source="cache",
            )

        try:
            used = self.client.count_today()
            soft = getattr(self.client, "SOFT_STOP", 1200)
            if used >= soft:
                logger.info(
                    "qa_generator skipped (budget): %s/%s",
                    used, soft,
                )
                return QAGeneratorResult(
                    answer=None, source="skipped",
                )
        except Exception:
            pass

        try:
            draft = self._call_llm(question, posting)
        except Exception as e:
            logger.warning("Gemma Q-answer failed: %s", e)
            return QAGeneratorResult(answer=None, source="skipped")

        return QAGeneratorResult(answer=draft, source="generated")

    def confirm_answer(self, question: str, answer: str) -> None:
        """Persist a user-confirmed (or user-edited) answer to the
        cache so future identical questions hit Tier 1 immediately."""
        key = _normalize_key(question)
        if not answer:
            return
        self._cache[key] = answer
        self._save_cache()

    def _call_llm(
        self, question: str, posting: Optional[dict],
    ) -> str:
        title = (posting or {}).get("title", "")
        employer = (posting or {}).get("employer", "")
        prompt = (
            f"CANDIDATE INVENTORY:\n{self.inventory_summary}\n\n"
            f"JOB: {title} at {employer}\n\n"
            f"QUESTION: {question}\n\n"
            "Write the answer now. Plain text only."
        )
        return self.client.generate(
            prompt=prompt, system=SYSTEM_PROMPT,
        ).strip()
