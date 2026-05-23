"""GET /api/cloud-usage/today — Google AI Studio daily budget.

Reads the shared engine.cloud_budget counter so the Applications
tab can show how much of the 1,500-call free-tier daily budget is
left, split by purpose (evaluation / resume / cover letter).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from dashboard.backend.deps import get_profile_id
from engine import cloud_budget

router = APIRouter(prefix="/api/cloud-usage", tags=["cloud-usage"])


class CloudUsageResponse(BaseModel):
    daily_limit: int
    total_used: int
    remaining: int
    breakdown: dict[str, int]


@router.get("/today", response_model=CloudUsageResponse)
def cloud_usage_today(
    profile_id: str = Depends(get_profile_id),
) -> CloudUsageResponse:
    used = cloud_budget.calls_used_today(profile_id)
    breakdown = cloud_budget.usage_breakdown_today(profile_id)
    return CloudUsageResponse(
        daily_limit=cloud_budget.DAILY_LIMIT,
        total_used=used,
        remaining=cloud_budget.calls_remaining_today(profile_id),
        breakdown=breakdown,
    )
