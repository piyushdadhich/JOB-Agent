"""Integration test: handler -> answer_custom_questions -> Tier 2.

Confirms that when QAMatcher returns None for a question and
ctx.qa_generator is set, the generator's answer is filled into
the linked input via target.fill().
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.applicant.handlers._common import (  # noqa: E402
    answer_custom_questions,
)
from engine.applicant.handlers.base import FillContext  # noqa: E402
from engine.applicant.profile import ApplicantProfile  # noqa: E402
from engine.applicant.qa_generator import QAGeneratorResult  # noqa: E402
from engine.applicant.qa_matcher import QAMatcher  # noqa: E402

from tests.test_greenhouse_handler import (  # noqa: E402
    MockLocator,
    MockPage,
)


def _ctx(tmp_path, qa_generator):
    resume = tmp_path / "r.docx"
    resume.write_text("R", encoding="utf-8")
    cl = tmp_path / "c.docx"
    cl.write_text("C", encoding="utf-8")
    return FillContext(
        posting={"id": 1, "title": "PM", "employer": "Acme"},
        profile=ApplicantProfile(),
        resume_path=resume, cover_letter_path=cl,
        resume_text="R", cover_letter_text="C",
        qa_matcher=QAMatcher(patterns=[]),  # no Tier 1 patterns
        qa_generator=qa_generator,
    )


def test_tier2_generator_fills_unmatched_question(tmp_path):
    label = MockLocator(
        text="Tell me about a time you led a difficult team.",
        attrs={"for": "behav"},
    )
    behav_input = MockLocator(tag="textarea")
    labels_list = MockLocator(children=[label])
    page = MockPage(selectors={
        "label": labels_list,
        "[id='behav']": behav_input,
    })

    qa_gen = MagicMock()
    qa_gen.answer.return_value = QAGeneratorResult(
        answer="Drafted by Gemma.", source="generated",
    )

    ctx = _ctx(tmp_path, qa_gen)
    answered, skipped = asyncio.run(
        answer_custom_questions(page, ctx.qa_matcher, ctx),
    )
    assert "Tell me about a time you led a difficult team." in answered
    behav_input.fill.assert_awaited_with("Drafted by Gemma.")
    assert skipped == []


def test_tier2_generator_returning_none_marks_skipped(tmp_path):
    label = MockLocator(
        text="Tell me about a time you led a difficult team.",
        attrs={"for": "behav"},
    )
    labels_list = MockLocator(children=[label])
    page = MockPage(selectors={"label": labels_list})

    qa_gen = MagicMock()
    qa_gen.answer.return_value = QAGeneratorResult(
        answer=None, source="skipped",
    )

    ctx = _ctx(tmp_path, qa_gen)
    answered, skipped = asyncio.run(
        answer_custom_questions(page, ctx.qa_matcher, ctx),
    )
    assert answered == {}
    assert any("Tell me about a time" in s for s in skipped)
