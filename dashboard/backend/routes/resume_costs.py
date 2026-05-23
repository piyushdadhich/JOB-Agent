"""Spec 8 TASK 4 — cost estimate endpoint for the Prompts tab."""
from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from engine.resume.cost_estimator import RATES, estimate_all

router = APIRouter(prefix="/api/resume", tags=["resume"])


class CostRow(BaseModel):
    provider: str
    per_app_usd: float
    monthly_usd: float
    annual_usd: float


class CostResponse(BaseModel):
    apps_per_week: int
    rows: list[CostRow]


@router.get("/cost-estimate", response_model=CostResponse)
def cost_estimate(
    apps_per_week: int = Query(5, ge=0, le=200),
) -> CostResponse:
    rows = estimate_all(apps_per_week=apps_per_week)
    return CostResponse(
        apps_per_week=apps_per_week,
        rows=[
            CostRow(
                provider=r.provider,
                per_app_usd=r.per_app_usd,
                monthly_usd=r.monthly_usd,
                annual_usd=r.annual_usd,
            )
            for r in rows
        ],
    )
