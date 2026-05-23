"""Spec 6 — scorer-threshold calibration routes.

Four endpoints:

  POST /api/calibrate/auto    — read current scoring distribution +
                                inventory size, return recommended
                                thresholds.
  GET  /api/calibrate/sample  — top 10 + bottom 10 currently-tiered
                                postings, for the guided-tune
                                yes/no review.
  POST /api/calibrate/confirm — apply guided_tune to one round of
                                user feedback, return new thresholds.
  POST /api/calibrate/apply   — persist thresholds to the profile
                                YAML AND re-bucket every eval_decisions
                                row (overall scores don't change,
                                only the tier label).

Thresholds live under the `tier_thresholds:` key in
config/profiles/{profile}.yaml; the scorer can be wired to read
them in a follow-up spec.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from dashboard.backend.deps import get_profile_id, get_tracker
from engine.matching.calibrator import (
    ScorerCalibrator, Thresholds, distribution_from_rows,
)
from engine.persistence.tracker import Tracker

router = APIRouter(prefix="/api/calibrate", tags=["calibrate"])


# --- Response models ---------------------------------------------

class ThresholdsModel(BaseModel):
    top: float
    strong: float
    exploratory: float


class DistributionModel(BaseModel):
    total: int
    top_tier_count: int
    strong_count: int
    exploratory_count: int
    skip_count: int


class AutoCalibrateResponse(BaseModel):
    thresholds: ThresholdsModel
    distribution: DistributionModel
    inventory_skill_count: int


class SamplePosting(BaseModel):
    opportunity_id: int
    employer: str
    title: str
    tier: str | None
    fit_score: float | None


class SampleResponse(BaseModel):
    top: list[SamplePosting]
    bottom: list[SamplePosting]


class ConfirmRequest(BaseModel):
    current: ThresholdsModel
    user_confirms_top: bool
    user_confirms_bottom: bool


class ConfirmResponse(BaseModel):
    thresholds: ThresholdsModel
    changed: bool


class ApplyRequest(BaseModel):
    thresholds: ThresholdsModel


class ApplyResponse(BaseModel):
    profile_yaml: str
    rebucketed: int


# --- Helpers -----------------------------------------------------

_calibrator = ScorerCalibrator()


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _profile_yaml_path(profile_id: str) -> Path:
    return _project_root() / "config" / "profiles" / f"{profile_id}.yaml"


def _inventory_skill_count(tracker: Tracker) -> int:
    """Best-effort skill-count for the active profile.

    Uses profile_skills (the canonical lightcast set) when populated,
    falls back to a rough split of career_inventory.md word-count
    by 8 (a slop estimate — 8 words per skill keyword) when not.
    """
    try:
        row = tracker._query_one(
            "SELECT COUNT(*) AS n FROM profile_skills "
            "WHERE taxonomy = 'lightcast'",
        )
        if row and row["n"]:
            return int(row["n"])
    except Exception:
        # profile_skills may not exist on a fresh DB; fall through.
        pass
    return 100  # neutral default: medium-tier seed


def _scoring_distribution(tracker: Tracker) -> dict:
    rows = tracker._query_all(
        "SELECT tier FROM eval_decisions "
        "WHERE id IN ("
        "  SELECT MAX(id) FROM eval_decisions GROUP BY opportunity_id"
        ")",
    )
    return distribution_from_rows([dict(r) for r in rows])


_SAMPLE_LIMIT = 10

_TOP_SQL = """\
SELECT e.opportunity_id, e.tier, e.fit_score,
       c.name AS employer, o.title AS title
FROM eval_decisions e
JOIN opportunities o ON o.id = e.opportunity_id
JOIN companies c ON c.id = o.company_id
WHERE e.id IN (
  SELECT MAX(id) FROM eval_decisions GROUP BY opportunity_id
)
ORDER BY e.fit_score DESC NULLS LAST
LIMIT ?
"""

_BOTTOM_SQL = """\
SELECT e.opportunity_id, e.tier, e.fit_score,
       c.name AS employer, o.title AS title
FROM eval_decisions e
JOIN opportunities o ON o.id = e.opportunity_id
JOIN companies c ON c.id = o.company_id
WHERE e.id IN (
  SELECT MAX(id) FROM eval_decisions GROUP BY opportunity_id
)
  AND e.fit_score IS NOT NULL
ORDER BY e.fit_score ASC
LIMIT ?
"""


def _row_to_sample(r: dict) -> SamplePosting:
    return SamplePosting(
        opportunity_id=int(r["opportunity_id"]),
        employer=str(r.get("employer") or ""),
        title=str(r.get("title") or ""),
        tier=r.get("tier"),
        fit_score=(
            float(r["fit_score"]) if r.get("fit_score") is not None else None
        ),
    )


# --- Routes ------------------------------------------------------

@router.post("/auto", response_model=AutoCalibrateResponse)
def auto_calibrate(
    tracker: Tracker = Depends(get_tracker),
) -> AutoCalibrateResponse:
    distribution = _scoring_distribution(tracker)
    skill_count = _inventory_skill_count(tracker)
    thresholds = _calibrator.auto_calibrate(skill_count, distribution)
    return AutoCalibrateResponse(
        thresholds=ThresholdsModel(**thresholds.as_dict()),
        distribution=DistributionModel(**distribution),
        inventory_skill_count=skill_count,
    )


@router.get("/sample", response_model=SampleResponse)
def calibrate_sample(
    tracker: Tracker = Depends(get_tracker),
) -> SampleResponse:
    # SQLite doesn't speak NULLS LAST natively pre-3.30; fall back
    # to a separate IS NOT NULL filter on the TOP query for safety.
    try:
        top_rows = tracker._query_all(_TOP_SQL, (_SAMPLE_LIMIT,))
    except Exception:
        top_rows = tracker._query_all(
            _TOP_SQL.replace("NULLS LAST", ""), (_SAMPLE_LIMIT,),
        )
    bottom_rows = tracker._query_all(_BOTTOM_SQL, (_SAMPLE_LIMIT,))
    return SampleResponse(
        top=[_row_to_sample(dict(r)) for r in top_rows],
        bottom=[_row_to_sample(dict(r)) for r in bottom_rows],
    )


@router.post("/confirm", response_model=ConfirmResponse)
def calibrate_confirm(body: ConfirmRequest) -> ConfirmResponse:
    current = Thresholds(
        top=body.current.top,
        strong=body.current.strong,
        exploratory=body.current.exploratory,
    )
    nxt = _calibrator.guided_tune(
        current,
        user_confirms_top=body.user_confirms_top,
        user_confirms_bottom=body.user_confirms_bottom,
    )
    return ConfirmResponse(
        thresholds=ThresholdsModel(**nxt.as_dict()),
        changed=(nxt != current),
    )


def _rebucket(tracker: Tracker, t: Thresholds) -> int:
    """UPDATE the tier label on every eval_decisions row to match
    the new thresholds. Overall scores stay the same — only the
    bucket changes. Returns the number of rows touched.
    """
    sql = (
        "UPDATE eval_decisions "
        "SET tier = CASE "
        "  WHEN fit_score >= ? THEN 'TOP_TIER' "
        "  WHEN fit_score >= ? THEN 'STRONG' "
        "  WHEN fit_score >= ? THEN 'EXPLORATORY' "
        "  ELSE 'SKIP' "
        "END"
    )
    cur = tracker._execute(sql, (t.top, t.strong, t.exploratory))
    return int(cur.rowcount or 0)


@router.post("/apply", response_model=ApplyResponse)
def calibrate_apply(
    body: ApplyRequest,
    profile_id: str = Depends(get_profile_id),
    tracker: Tracker = Depends(get_tracker),
) -> ApplyResponse:
    new = Thresholds(
        top=body.thresholds.top,
        strong=body.thresholds.strong,
        exploratory=body.thresholds.exploratory,
    ).clamped()

    yaml_path = _profile_yaml_path(profile_id)
    if not yaml_path.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                f"profile YAML {yaml_path} not found — run the "
                "onboarding wizard first."
            ),
        )
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    data["tier_thresholds"] = new.as_dict()
    yaml_path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    rebucketed = _rebucket(tracker, new)

    root = _project_root()
    return ApplyResponse(
        profile_yaml=str(yaml_path.relative_to(root)),
        rebucketed=rebucketed,
    )
