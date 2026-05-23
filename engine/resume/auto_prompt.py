"""Auto-generate resume + cover-letter prompts for shortlisted postings.

Called by the cloud pipeline after evaluation. Reuses PromptService --
the same wrapper the dashboard's on-demand /api/prompts/{id}/resume
endpoint uses -- so auto-generated prompts are byte-identical to
on-demand ones. Pure template assembly, zero API calls.

After this runs, the dashboard's Prompts queue is populated for the
day's TOP_TIER / STRONG postings before the user opens it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from dashboard.backend.services.prompt_service import PromptService
from engine.persistence.tracker import Tracker

logger = logging.getLogger(__name__)


def auto_generate_prompts(
    tracker: Tracker,
    opportunity_ids: list[int],
    profile_id: str,
    prompt_service: Optional[PromptService] = None,
) -> dict:
    """Build and persist resume + CL prompts for each opportunity.

    For each opp_id:
      - Skip silently if the opportunity row is missing.
      - Find the latest application for the posting; create one
        (resume_variant="dashboard_pending") if none exists.
      - Skip if both prompts are already populated (idempotent).
      - Build resume + CL prompts via PromptService.
      - Stamp `selected_at` (so the row appears in the Prompts queue)
        and write both prompts via update_application_fields.

    Returns {"generated": int, "skipped_existing": int, "skipped_missing": int}.
    """
    service = prompt_service or PromptService()
    generated = 0
    skipped_existing = 0
    skipped_missing = 0

    for opp_id in opportunity_ids:
        posting = tracker.get_opportunity_by_id(opp_id)
        if posting is None:
            skipped_missing += 1
            continue

        app_id = _latest_application_id(tracker, opp_id)
        if app_id is None:
            app_id = tracker.create_application(
                opportunity_id=opp_id,
                resume_variant="dashboard_pending",
            )
            existing_app = None
        else:
            existing_app = tracker.get_application_by_id(app_id)
            if (
                existing_app
                and existing_app.get("resume_prompt")
                and existing_app.get("cover_letter_prompt")
            ):
                skipped_existing += 1
                continue

        eval_d = tracker.get_latest_evaluation(opp_id)

        try:
            resume_prompt = service.build_resume_prompt(
                profile_id=profile_id,
                posting=posting,
                eval_decision=eval_d,
            )
            cl_prompt = service.build_cover_letter_prompt(
                profile_id=profile_id,
                posting=posting,
                eval_decision=eval_d,
            )
        except Exception:
            logger.exception(
                "auto_prompt build failed for opportunity %s", opp_id,
            )
            continue

        now_iso = datetime.now(timezone.utc).isoformat()
        fields: dict = {
            "resume_prompt": resume_prompt,
            "cover_letter_prompt": cl_prompt,
        }
        if not (existing_app and existing_app.get("selected_at")):
            fields["selected_at"] = now_iso
        tracker.update_application_fields(app_id, **fields)
        generated += 1

    return {
        "generated": generated,
        "skipped_existing": skipped_existing,
        "skipped_missing": skipped_missing,
    }


def _latest_application_id(tracker: Tracker, opp_id: int) -> Optional[int]:
    row = tracker._query_one(
        "SELECT id FROM applications WHERE opportunity_id = ? "
        "ORDER BY status_updated_at DESC LIMIT 1",
        (opp_id,),
    )
    return row["id"] if row else None
