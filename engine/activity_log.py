"""System-wide activity logger (Spec FIX-6).

Every significant action — discovery, evaluation, generation,
application, scheduled-task runs — writes one row to the
activity_log table. The dashboard's Activity feed reads it.

Fire-and-forget by design: a logging failure (locked DB, missing
table on an un-migrated profile) never propagates to the caller,
so instrumenting an action can't break the action.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from engine.persistence.tracker import Tracker

logger = logging.getLogger(__name__)

CATEGORIES = (
    "discovery", "evaluation", "generation",
    "application", "system", "scheduled_task",
)
STATUSES = ("success", "error", "running", "warning")


def log_activity(
    profile_id: str,
    category: str,
    action: str,
    status: str,
    summary: str,
    details: Optional[dict] = None,
    opportunity_id: Optional[int] = None,
    duration_ms: Optional[int] = None,
    error_message: Optional[str] = None,
) -> None:
    """Append one row to activity_log. Best-effort — never raises."""
    try:
        tracker = Tracker(profile_id)
        try:
            tracker._execute(
                "INSERT INTO activity_log "
                "(category, action, status, summary, details, "
                " opportunity_id, duration_ms, error_message) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    category,
                    action,
                    status,
                    summary,
                    json.dumps(details) if details is not None else None,
                    opportunity_id,
                    duration_ms,
                    error_message,
                ),
            )
        finally:
            tracker.close()
    except Exception as e:  # noqa: BLE001 — logging must never block
        logger.warning("activity log write failed: %s", e)
