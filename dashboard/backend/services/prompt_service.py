"""Wrap the existing prompt builders for the dashboard.

The dashboard generates resume + cover letter prompts on demand; this
service is a thin orchestrator that:
  - loads the candidate's career inventory + extract + profile config
    from their on-disk locations,
  - assembles them into a posting-specific prompt via the existing
    ResumePromptBuilder / CoverLetterPromptBuilder,
  - returns the prompt string for the dashboard to display.

Gap analysis (a 16s Gemma call) is intentionally NOT run here — the
dashboard would block too long. Pass `gap_report=None` and the
builders gracefully omit the gap-aware guidance section. Add a
separate `/api/gap-analysis` route in a later phase if we want it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import yaml

from engine.resume.cover_letter_prompt_builder import (
    CoverLetterPromptBuilder,
)
from engine.resume.prompt_builder import ResumePromptBuilder

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _inventory_text(profile_id: str) -> str:
    """Career inventory markdown — falls back to '' if missing.

    A missing inventory still produces a usable prompt (the builder
    inserts '(inventory not provided)'), so the dashboard can show
    something on first run before the user authors their inventory.
    """
    path = (
        PROJECT_ROOT / "source_materials" / profile_id
        / "career_inventory.md"
    )
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _inventory_extract(profile_id: str) -> Optional[dict]:
    path = (
        PROJECT_ROOT / "data" / profile_id / "inventory_extract.json"
    )
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _profile_config(profile_id: str) -> dict:
    path = (
        PROJECT_ROOT / "config" / "profiles" / f"{profile_id}.yaml"
    )
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _applicant_profile(profile_id: str) -> Optional[dict]:
    """Parsed config/profiles/{profile_id}_applicant.yaml — the PII
    block (name, email, phone, location, links, education, certs)
    the prompts inject as a CANDIDATE CONTACT section. Returns None
    when the file is absent so the prompt still assembles."""
    path = (
        PROJECT_ROOT / "config" / "profiles"
        / f"{profile_id}_applicant.yaml"
    )
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


class PromptService:
    """Pure assembly. No I/O beyond reading the on-disk inventory."""

    def __init__(
        self,
        resume_builder: Optional[ResumePromptBuilder] = None,
        cover_letter_builder: Optional[CoverLetterPromptBuilder] = None,
    ):
        self._resume_builder = resume_builder or ResumePromptBuilder()
        self._cover_letter_builder = (
            cover_letter_builder or CoverLetterPromptBuilder()
        )

    def build_resume_prompt(
        self,
        *,
        profile_id: str,
        posting: dict,
        eval_decision: Optional[dict] = None,
    ) -> str:
        return self._resume_builder.build_prompt(
            posting=posting,
            eval_decision=eval_decision,
            gap_report=None,
            inventory_text=_inventory_text(profile_id),
            inventory_extract=_inventory_extract(profile_id),
            profile_config=_profile_config(profile_id),
            applicant_profile=_applicant_profile(profile_id),
        )

    def build_cover_letter_prompt(
        self,
        *,
        profile_id: str,
        posting: dict,
        eval_decision: Optional[dict] = None,
    ) -> str:
        return self._cover_letter_builder.build_prompt(
            posting=posting,
            eval_decision=eval_decision,
            gap_report=None,
            inventory_text=_inventory_text(profile_id),
            inventory_extract=_inventory_extract(profile_id),
            profile_config=_profile_config(profile_id),
            applicant_profile=_applicant_profile(profile_id),
        )
