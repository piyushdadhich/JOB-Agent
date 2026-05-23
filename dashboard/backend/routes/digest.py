"""Spec 13 TASK 2 — daily digest endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from dashboard.backend.deps import get_tracker
from engine.digest.generator import generate
from engine.persistence.tracker import Tracker

router = APIRouter(prefix="/api/digest", tags=["digest"])


class DigestResponse(BaseModel):
    window_hours: int
    new_top_tier: int
    new_strong: int
    new_exploratory: int
    needs_followup: int
    generated_at: str


@router.get("/today", response_model=DigestResponse)
def digest_today(
    window_hours: int = Query(24, ge=1, le=168),
    tracker: Tracker = Depends(get_tracker),
) -> DigestResponse:
    payload = generate(tracker, window_hours=window_hours)
    return DigestResponse(**payload.as_dict())
