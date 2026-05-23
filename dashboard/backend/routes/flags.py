"""POST /api/shortlist/{id}/flag + flag_rules CRUD (v2.12).

Flagging a posting (single transaction in tracker.flag_opportunity_atomic):
  1. INSERT eval_labels (verdict='skip', reason='user_flagged')
  2. UPDATE latest eval_decisions row to tier='SKIP', flagged_at=now
  3. If create_rule: INSERT flag_rules with the supplied patterns
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from dashboard.backend.deps import get_tracker
from engine.persistence.tracker import Tracker

router = APIRouter(tags=["flags"])


class FlagRuleSpec(BaseModel):
    employer_pattern: Optional[str] = None
    title_pattern: Optional[str] = None
    industry_pattern: Optional[str] = None
    function_pattern: Optional[str] = None
    ai_subtype_pattern: Optional[str] = None


class FlagRequest(BaseModel):
    create_rule: bool = False
    rule: Optional[FlagRuleSpec] = None


class FlagResponse(BaseModel):
    opportunity_id: int
    rule_id: Optional[int] = None


@router.post(
    "/api/shortlist/{opportunity_id}/flag",
    response_model=FlagResponse,
)
def flag_posting(
    opportunity_id: int,
    body: FlagRequest,
    tracker: Tracker = Depends(get_tracker),
) -> FlagResponse:
    if tracker.get_opportunity_by_id(opportunity_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"opportunity {opportunity_id} not found",
        )

    rule_payload = None
    if body.create_rule:
        if body.rule is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "rule fields must be provided when create_rule=true"
                ),
            )
        non_null = sum(
            1 for v in (
                body.rule.employer_pattern,
                body.rule.title_pattern,
                body.rule.industry_pattern,
                body.rule.function_pattern,
                body.rule.ai_subtype_pattern,
            ) if v is not None
        )
        if non_null == 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    "rule must have at least one non-null pattern; "
                    "an all-null rule would auto-skip every future "
                    "posting"
                ),
            )
        rule_payload = body.rule.model_dump()

    now = datetime.now(timezone.utc).isoformat()
    result = tracker.flag_opportunity_atomic(
        opportunity_id=opportunity_id,
        flagged_at=now,
        rule=rule_payload,
    )
    return FlagResponse(
        opportunity_id=opportunity_id,
        rule_id=result.get("rule_id"),
    )


class FlagRuleRow(BaseModel):
    id: int
    employer_pattern: Optional[str] = None
    title_pattern: Optional[str] = None
    industry_pattern: Optional[str] = None
    function_pattern: Optional[str] = None
    ai_subtype_pattern: Optional[str] = None
    source_opportunity_id: Optional[int] = None
    created_at: str
    active: int


@router.get("/api/flags/rules", response_model=list[FlagRuleRow])
def list_rules(
    tracker: Tracker = Depends(get_tracker),
) -> list[FlagRuleRow]:
    return [
        FlagRuleRow(**r)
        for r in tracker.list_flag_rules(active_only=False)
    ]


@router.post("/api/flags/rules/{rule_id}/disable")
def disable_rule(
    rule_id: int,
    tracker: Tracker = Depends(get_tracker),
) -> dict:
    if not tracker.set_flag_rule_active(rule_id, False):
        raise HTTPException(
            status_code=404, detail=f"flag_rule {rule_id} not found",
        )
    return {"rule_id": rule_id, "active": False}


@router.post("/api/flags/rules/{rule_id}/enable")
def enable_rule(
    rule_id: int,
    tracker: Tracker = Depends(get_tracker),
) -> dict:
    if not tracker.set_flag_rule_active(rule_id, True):
        raise HTTPException(
            status_code=404, detail=f"flag_rule {rule_id} not found",
        )
    return {"rule_id": rule_id, "active": True}
