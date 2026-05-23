"""Save / recall application records (v2.9 columns).

Phase 11's per-ATS handlers call save_application after a
successful submission; recall_application reads back the full
text so the user can re-render or audit.
"""
from __future__ import annotations

import json
from typing import Optional


def save_application(
    tracker,
    *,
    opportunity_id: int,
    resume_variant: str,
    resume_text: str,
    cover_letter_text: str,
    ats_platform: str,
    screenshot_path: Optional[str] = None,
    submitted_url: Optional[str] = None,
    screening_answers: Optional[dict] = None,
    submitted_via: str = "direct",
    final_status: str = "submitted",
) -> int:
    """Insert a fully-submitted application row.

    Creates the row at status='drafted' (per existing tracker
    contract) then transitions to `final_status`. The v2.9 columns
    are populated via update_application_fields; status_history
    rows are written by update_application_status.
    """
    app_id = tracker.create_application(
        opportunity_id=opportunity_id,
        resume_variant=resume_variant,
        submitted_via=submitted_via,
    )
    tracker.update_application_fields(
        app_id,
        resume_text=resume_text,
        cover_letter_text=cover_letter_text,
        ats_platform=ats_platform,
        screenshot_path=screenshot_path,
        submitted_url=submitted_url,
        screening_answers=(
            json.dumps(screening_answers) if screening_answers else None
        ),
    )
    if final_status != "drafted":
        tracker.update_application_status(app_id, final_status)
    return app_id


def recall_application(
    tracker, *, opportunity_id: int,
) -> Optional[dict]:
    """Return the most recent application row for an opportunity,
    decoded (screening_answers parsed back to dict, employer joined
    in). Returns None if no application exists.
    """
    rows = tracker._query_all(
        "SELECT a.*, "
        "       o.title AS opportunity_title, "
        "       c.name AS employer "
        "FROM applications a "
        "JOIN opportunities o ON o.id = a.opportunity_id "
        "JOIN companies c ON c.id = o.company_id "
        "WHERE a.opportunity_id = ? "
        "ORDER BY a.status_updated_at DESC LIMIT 1",
        (opportunity_id,),
    )
    if not rows:
        return None
    row = dict(rows[0])
    if row.get("screening_answers"):
        try:
            row["screening_answers"] = json.loads(row["screening_answers"])
        except (TypeError, json.JSONDecodeError):
            pass
    return row
