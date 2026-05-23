"""Spec F1 TASK 1 — Expansion Insights API.

Surfaces the Phase 7 ExpansionOrchestrator output (Spec D1) to the
dashboard. Read-only by default — confirm / reject / watch / action
calls layer a small overlay JSON over the orchestrator output so the
user's decisions persist across re-runs.

Cluster status, skill-gap actions, and deep-target watch state are
not stored in eval_decisions / opportunities — they're meta-decisions
about FUTURE discovery, not per-posting evaluations. Persistence:
  - cluster status + skill_gap actions: JSON overlay file at
    scripts/output/expansion_state_{profile_id}.json
  - deep-target watch state: companies.is_deep_target column (v2.16),
    via tracker.set_company_deep_target

On-demand run (`POST /api/expansion/run`) regenerates the cached
report. GET endpoints reuse the cached report; if no cache exists
the first GET implicitly runs the orchestrator.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from dashboard.backend.deps import get_profile_id, get_tracker
from engine.expansion.orchestrator import (
    ExpansionOrchestrator, ExpansionReport,
)
from engine.matching.scorer import load_inventory_skill_ids
from engine.persistence.tracker import Tracker

router = APIRouter(prefix="/api/expansion", tags=["expansion"])

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_STATE_DIR = PROJECT_ROOT / "scripts" / "output"
_DEFAULT_DAYS_BACK = 14


# --- State file helpers ------------------------------------------

def _state_path(profile_id: str) -> Path:
    return _STATE_DIR / f"expansion_state_{profile_id}.json"


def _slug(s: str) -> str:
    """Slugify a modal title for use as a stable cluster id."""
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "untitled"


def _empty_state() -> dict:
    return {
        "generated_at": None,
        "days_analyzed": _DEFAULT_DAYS_BACK,
        "report": None,
        "cluster_status": {},
        "skill_gap_actions": {},
    }


def _load_state(profile_id: str) -> dict:
    path = _state_path(profile_id)
    if not path.exists():
        return _empty_state()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _empty_state()


def _save_state(profile_id: str, state: dict) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _state_path(profile_id).write_text(
        json.dumps(state, indent=2, default=str), encoding="utf-8",
    )


def _profile_yaml(profile_id: str) -> dict:
    path = (
        PROJECT_ROOT / "config" / "profiles" / f"{profile_id}.yaml"
    )
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}


def _serialize_report(report: ExpansionReport) -> dict:
    """Serialize an ExpansionReport to a JSON-safe dict."""
    return {
        "generated_at": report.generated_at.isoformat(),
        "days_analyzed": report.days_analyzed,
        "title_clusters": [
            {
                "modal_title": c.modal_title,
                "posting_count": c.posting_count,
                "skill_profile": sorted(c.skill_profile),
                "example_employers": list(c.example_employers),
                "confidence": c.confidence,
                "already_in_target": bool(c.already_in_target),
            }
            for c in report.title_clusters
        ],
        "deep_targets": [
            {
                "company_name": t.company_name,
                "company_id": t.company_id,
                "strong_count": t.strong_count,
                "top_tier_count": t.top_tier_count,
                "total_scored": t.total_scored,
                "ats_platform": t.ats_platform,
                "ats_slug": t.ats_slug,
                "already_watched": bool(t.already_watched),
            }
            for t in report.deep_targets
        ],
        "adjacent_suggestions": [
            asdict(s) for s in report.adjacent_suggestions
        ],
        "skill_gaps": [
            {
                "skill_id": g.skill_id,
                "skill_label": g.skill_label,
                "occurrence_count": g.occurrence_count,
                "example_postings": list(g.example_postings),
                "action_prompt": g.action_prompt,
            }
            for g in report.skill_gaps
        ],
    }


def _run_orchestrator(
    tracker: Tracker,
    profile_id: str,
    days_back: int = _DEFAULT_DAYS_BACK,
) -> dict:
    """Run the Phase 7 orchestrator and persist the resulting state."""
    profile = _profile_yaml(profile_id)
    try:
        inventory = load_inventory_skill_ids(tracker, profile_id)
    except Exception:
        inventory = set()
    orchestrator = ExpansionOrchestrator(
        tracker=tracker, profile=profile, inventory_skill_ids=inventory,
    )
    report = orchestrator.run(days_back=days_back)
    state = _load_state(profile_id)
    state["generated_at"] = datetime.now(timezone.utc).isoformat()
    state["days_analyzed"] = days_back
    state["report"] = _serialize_report(report)
    _save_state(profile_id, state)
    return state


def _ensure_state(tracker: Tracker, profile_id: str) -> dict:
    """Return current state, running the orchestrator if no cache exists."""
    state = _load_state(profile_id)
    if state.get("report") is None:
        return _run_orchestrator(tracker, profile_id)
    return state


# --- Response models ---------------------------------------------

class ExpansionSummary(BaseModel):
    generated_at: Optional[str]
    days_analyzed: int
    title_clusters_count: int
    deep_targets_count: int
    adjacent_suggestions_count: int
    skill_gaps_count: int


class TitleClusterRow(BaseModel):
    id: str
    modal_title: str
    posting_count: int
    example_employers: list[str]
    confidence: float
    already_in_target: bool
    status: Literal["pending", "confirmed", "rejected"]


class DeepTargetRow(BaseModel):
    company_id: int
    company_name: str
    strong_count: int
    top_tier_count: int
    ats_platform: Optional[str] = None
    ats_slug: Optional[str] = None
    is_watched: bool


class SkillGapRow(BaseModel):
    skill_id: str
    skill_label: str
    occurrence_count: int
    example_postings: list[str]
    action: Optional[Literal["in_inventory", "to_learn"]] = None


class RunResponse(BaseModel):
    status: str
    message: str


class GapActionRequest(BaseModel):
    action: Literal["in_inventory", "to_learn"]


# --- GET endpoints -----------------------------------------------

@router.get("/summary", response_model=ExpansionSummary)
def get_summary(
    tracker: Tracker = Depends(get_tracker),
    profile_id: str = Depends(get_profile_id),
) -> ExpansionSummary:
    state = _ensure_state(tracker, profile_id)
    report = state.get("report") or {}
    return ExpansionSummary(
        generated_at=state.get("generated_at"),
        days_analyzed=state.get("days_analyzed", _DEFAULT_DAYS_BACK),
        title_clusters_count=len(report.get("title_clusters") or []),
        deep_targets_count=len(report.get("deep_targets") or []),
        adjacent_suggestions_count=len(
            report.get("adjacent_suggestions") or []
        ),
        skill_gaps_count=len(report.get("skill_gaps") or []),
    )


@router.get("/title-clusters", response_model=list[TitleClusterRow])
def get_title_clusters(
    tracker: Tracker = Depends(get_tracker),
    profile_id: str = Depends(get_profile_id),
) -> list[TitleClusterRow]:
    state = _ensure_state(tracker, profile_id)
    report = state.get("report") or {}
    status_map = state.get("cluster_status") or {}
    out: list[TitleClusterRow] = []
    for c in report.get("title_clusters") or []:
        cid = _slug(c["modal_title"])
        out.append(TitleClusterRow(
            id=cid,
            modal_title=c["modal_title"],
            posting_count=c["posting_count"],
            example_employers=c.get("example_employers") or [],
            confidence=c["confidence"],
            already_in_target=bool(c.get("already_in_target")),
            status=status_map.get(cid, "pending"),
        ))
    return out


@router.get("/deep-targets", response_model=list[DeepTargetRow])
def get_deep_targets(
    tracker: Tracker = Depends(get_tracker),
    profile_id: str = Depends(get_profile_id),
) -> list[DeepTargetRow]:
    state = _ensure_state(tracker, profile_id)
    report = state.get("report") or {}
    # Refresh is_watched from companies.is_deep_target (canonical source).
    watched_ids: set[int] = set()
    rows = tracker._query_all(
        "SELECT id FROM companies WHERE is_deep_target = 1",
    )
    for r in rows:
        watched_ids.add(r["id"])
    out: list[DeepTargetRow] = []
    for t in report.get("deep_targets") or []:
        out.append(DeepTargetRow(
            company_id=t["company_id"],
            company_name=t["company_name"],
            strong_count=t["strong_count"],
            top_tier_count=t["top_tier_count"],
            ats_platform=t.get("ats_platform"),
            ats_slug=t.get("ats_slug"),
            is_watched=t["company_id"] in watched_ids,
        ))
    return out


@router.get("/skill-gaps", response_model=list[SkillGapRow])
def get_skill_gaps(
    tracker: Tracker = Depends(get_tracker),
    profile_id: str = Depends(get_profile_id),
) -> list[SkillGapRow]:
    state = _ensure_state(tracker, profile_id)
    report = state.get("report") or {}
    actions = state.get("skill_gap_actions") or {}
    out: list[SkillGapRow] = []
    for g in report.get("skill_gaps") or []:
        out.append(SkillGapRow(
            skill_id=g["skill_id"],
            skill_label=g["skill_label"],
            occurrence_count=g["occurrence_count"],
            example_postings=g.get("example_postings") or [],
            action=actions.get(g["skill_id"]),
        ))
    return out


# --- Spec 13 TASK 4: /api/gaps dashboard alias ------------------

_dashboard_gaps_router = APIRouter(prefix="/api/gaps", tags=["gaps"])


@_dashboard_gaps_router.get("", response_model=list[SkillGapRow])
def get_gaps_top(
    limit: int = 10,
    tracker: Tracker = Depends(get_tracker),
    profile_id: str = Depends(get_profile_id),
) -> list[SkillGapRow]:
    """Dashboard-friendly alias of /api/expansion/skill-gaps.

    Same data as the expansion-tab feed, capped at `limit` for a
    compact landing-page card. The full list is one tab away."""
    rows = get_skill_gaps(tracker, profile_id)
    return rows[: max(0, int(limit))]


router.routes.extend(_dashboard_gaps_router.routes)


# --- POST endpoints ----------------------------------------------

def _find_cluster(state: dict, cluster_id: str) -> Optional[dict]:
    report = state.get("report") or {}
    for c in report.get("title_clusters") or []:
        if _slug(c["modal_title"]) == cluster_id:
            return c
    return None


def _find_skill_gap(state: dict, skill_id: str) -> Optional[dict]:
    report = state.get("report") or {}
    for g in report.get("skill_gaps") or []:
        if g["skill_id"] == skill_id:
            return g
    return None


@router.post(
    "/title-clusters/{cluster_id}/confirm",
    response_model=TitleClusterRow,
)
def confirm_cluster(
    cluster_id: str,
    tracker: Tracker = Depends(get_tracker),
    profile_id: str = Depends(get_profile_id),
) -> TitleClusterRow:
    state = _ensure_state(tracker, profile_id)
    cluster = _find_cluster(state, cluster_id)
    if cluster is None:
        raise HTTPException(
            status_code=404,
            detail=f"cluster {cluster_id!r} not found in latest report",
        )
    state.setdefault("cluster_status", {})[cluster_id] = "confirmed"
    _save_state(profile_id, state)
    return TitleClusterRow(
        id=cluster_id,
        modal_title=cluster["modal_title"],
        posting_count=cluster["posting_count"],
        example_employers=cluster.get("example_employers") or [],
        confidence=cluster["confidence"],
        already_in_target=bool(cluster.get("already_in_target")),
        status="confirmed",
    )


@router.post(
    "/title-clusters/{cluster_id}/reject",
    response_model=TitleClusterRow,
)
def reject_cluster(
    cluster_id: str,
    tracker: Tracker = Depends(get_tracker),
    profile_id: str = Depends(get_profile_id),
) -> TitleClusterRow:
    state = _ensure_state(tracker, profile_id)
    cluster = _find_cluster(state, cluster_id)
    if cluster is None:
        raise HTTPException(
            status_code=404,
            detail=f"cluster {cluster_id!r} not found in latest report",
        )
    state.setdefault("cluster_status", {})[cluster_id] = "rejected"
    _save_state(profile_id, state)
    return TitleClusterRow(
        id=cluster_id,
        modal_title=cluster["modal_title"],
        posting_count=cluster["posting_count"],
        example_employers=cluster.get("example_employers") or [],
        confidence=cluster["confidence"],
        already_in_target=bool(cluster.get("already_in_target")),
        status="rejected",
    )


def _company_row(tracker: Tracker, company_id: int) -> Optional[dict]:
    row = tracker._query_one(
        "SELECT id, name, ats_platform, ats_slug, is_deep_target "
        "FROM companies WHERE id = ?",
        (company_id,),
    )
    return dict(row) if row else None


@router.post(
    "/deep-targets/{company_id}/watch",
    response_model=DeepTargetRow,
)
def watch_deep_target(
    company_id: int,
    tracker: Tracker = Depends(get_tracker),
    profile_id: str = Depends(get_profile_id),
) -> DeepTargetRow:
    row = _company_row(tracker, company_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"company {company_id} not found",
        )
    tracker.set_company_deep_target(company_id, True)
    state = _load_state(profile_id)
    target = next(
        (
            t for t in (state.get("report") or {}).get("deep_targets") or []
            if t["company_id"] == company_id
        ),
        None,
    )
    return DeepTargetRow(
        company_id=company_id,
        company_name=row["name"],
        strong_count=(target or {}).get("strong_count", 0),
        top_tier_count=(target or {}).get("top_tier_count", 0),
        ats_platform=row.get("ats_platform"),
        ats_slug=row.get("ats_slug"),
        is_watched=True,
    )


@router.post(
    "/deep-targets/{company_id}/unwatch",
    response_model=DeepTargetRow,
)
def unwatch_deep_target(
    company_id: int,
    tracker: Tracker = Depends(get_tracker),
    profile_id: str = Depends(get_profile_id),
) -> DeepTargetRow:
    row = _company_row(tracker, company_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"company {company_id} not found",
        )
    tracker.set_company_deep_target(company_id, False)
    state = _load_state(profile_id)
    target = next(
        (
            t for t in (state.get("report") or {}).get("deep_targets") or []
            if t["company_id"] == company_id
        ),
        None,
    )
    return DeepTargetRow(
        company_id=company_id,
        company_name=row["name"],
        strong_count=(target or {}).get("strong_count", 0),
        top_tier_count=(target or {}).get("top_tier_count", 0),
        ats_platform=row.get("ats_platform"),
        ats_slug=row.get("ats_slug"),
        is_watched=False,
    )


@router.post(
    "/skill-gaps/{skill_id}/action",
    response_model=SkillGapRow,
)
def set_skill_gap_action(
    skill_id: str,
    body: GapActionRequest,
    tracker: Tracker = Depends(get_tracker),
    profile_id: str = Depends(get_profile_id),
) -> SkillGapRow:
    state = _ensure_state(tracker, profile_id)
    gap = _find_skill_gap(state, skill_id)
    if gap is None:
        raise HTTPException(
            status_code=404,
            detail=f"skill_gap {skill_id!r} not found in latest report",
        )
    state.setdefault("skill_gap_actions", {})[skill_id] = body.action
    _save_state(profile_id, state)
    return SkillGapRow(
        skill_id=skill_id,
        skill_label=gap["skill_label"],
        occurrence_count=gap["occurrence_count"],
        example_postings=gap.get("example_postings") or [],
        action=body.action,
    )


@router.post("/run", response_model=RunResponse)
def run_expansion(
    tracker: Tracker = Depends(get_tracker),
    profile_id: str = Depends(get_profile_id),
) -> RunResponse:
    state = _run_orchestrator(tracker, profile_id)
    return RunResponse(
        status="started",
        message=(
            f"Analyzed last {state.get('days_analyzed', _DEFAULT_DAYS_BACK)}"
            " days."
        ),
    )
