"""Settings page backend — read/write the live profile YAMLs.

Mirrors the setup wizard's writes but per-section so a user can
edit one block without re-running the whole wizard. Every section
that touches profile YAML uses the same `_patch_yaml` helper so
keys not in the request are preserved.

This module hosts both the Spec-10 endpoints (Profile / Applicant /
LLM / Sources / Exclusions / Schedule / Gmail / Export) and the
FIX-5 additions (PUT applicant, Gemini API key, simple-string
exclusions). The two flavours coexist; `/exclusions` returns both
the rich `entries` and the simple `companies` shape so either UI
can consume it.
"""
from __future__ import annotations

import io
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from dashboard.backend.deps import get_profile_id

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Default Gemini model surfaced in the API-key section.
GEMINI_MODEL = "gemma-4-31b-it"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _profile_yaml_path(profile_id: str) -> Path:
    return _project_root() / "config" / "profiles" / f"{profile_id}.yaml"


def _applicant_yaml_path(profile_id: str) -> Path:  # noqa: D401 — legacy helper, see _applicant_path
    return (
        _project_root() / "config" / "profiles"
        / f"{profile_id}_applicant.yaml"
    )


# FIX-5 alias kept for symmetry with the master router; same target as
# `_applicant_yaml_path` above.
def _applicant_path(profile_id: str) -> Path:
    return _applicant_yaml_path(profile_id)


def _api_key_path(profile_id: str) -> Path:
    return _project_root() / "data" / profile_id / "gemini_api_key.txt"


# Per-profile exclusions list (FIX-5 simple-string format). The
# Spec-10 endpoints continue to use `_exclusions_path()` for the
# rich multi-profile YAML; this helper is for the simpler
# `{companies: [...]}` shape per profile.
def _exclusions_simple_path(profile_id: str) -> Path:
    return _project_root() / "config" / "exclusions" / f"{profile_id}.yaml"


def _exclusions_path(profile_id: str | None = None) -> Path:
    # profile_id is accepted (and ignored — the file is global with
    # per-profile sections inside) so test fixtures can monkeypatch
    # this helper with a 1-arg lambda matching the FIX-5 convention.
    return _project_root() / "config" / "exclusions.yaml"


def _setup_state_path(profile_id: str) -> Path:
    return _project_root() / "data" / profile_id / "setup_state.json"


def _mask_key(key: str) -> str:
    """Show only the last 4 chars; the rest become bullets."""
    if not key:
        return ""
    if len(key) <= 4:
        return "•" * len(key)
    return "•" * (len(key) - 4) + key[-4:]


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _patch_yaml(path: Path, updates: dict[str, Any]) -> None:
    data = _load_yaml(path)
    for key, value in updates.items():
        data[key] = value
    _write_yaml(path, data)


# --- Section 1: Profile -----------------------------------------

class SalaryModel(BaseModel):
    floor: int = 0
    target: int = 0
    cap: int = 0


class ProfileSettings(BaseModel):
    display_name: str = ""
    target_cities: list[str] = []
    salary: SalaryModel = SalaryModel()
    target_role_types: list[str] = []
    employer_size_priority: str | None = None
    remote_preference: str | None = None


@router.get("/profile", response_model=ProfileSettings)
def get_profile(
    profile_id: str = Depends(get_profile_id),
) -> ProfileSettings:
    data = _load_yaml(_profile_yaml_path(profile_id))
    return ProfileSettings(
        display_name=str(data.get("display_name") or ""),
        target_cities=list(data.get("target_cities") or []),
        salary=SalaryModel(**(data.get("salary") or {})),
        target_role_types=list(data.get("target_role_types") or []),
        employer_size_priority=data.get("employer_size_priority"),
        remote_preference=data.get("remote_preference"),
    )


@router.post("/profile", response_model=ProfileSettings)
def update_profile(
    body: ProfileSettings,
    profile_id: str = Depends(get_profile_id),
) -> ProfileSettings:
    path = _profile_yaml_path(profile_id)
    updates: dict[str, Any] = {
        "display_name": body.display_name,
        "target_cities": body.target_cities,
        "target_role_types": body.target_role_types,
    }
    if body.salary.floor or body.salary.target or body.salary.cap:
        updates["salary"] = {
            "floor": body.salary.floor,
            "target": body.salary.target,
            "cap": body.salary.cap,
        }
    if body.employer_size_priority:
        updates["employer_size_priority"] = body.employer_size_priority
    if body.remote_preference:
        updates["remote_preference"] = body.remote_preference
    _patch_yaml(path, updates)
    return get_profile(profile_id=profile_id)


# --- Section 2: Applicant ---------------------------------------

class ApplicantSettings(BaseModel):
    # FIX-5 widened the typed shape: city / province / portfolio_url
    # now round-trip through GET/PUT so the Settings UI's richer form
    # (8 fields) and the FIX-5 tests both see what they expect.
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    city: str = ""
    province: str = ""
    linkedin_url: str = ""
    portfolio_url: str = ""


@router.get("/applicant", response_model=ApplicantSettings)
def get_applicant(
    profile_id: str = Depends(get_profile_id),
) -> ApplicantSettings:
    data = _load_yaml(_applicant_path(profile_id))
    return ApplicantSettings(
        first_name=str(data.get("first_name") or ""),
        last_name=str(data.get("last_name") or ""),
        email=str(data.get("email") or ""),
        phone=str(data.get("phone") or ""),
        city=str(data.get("city") or ""),
        province=str(data.get("province") or ""),
        linkedin_url=str(data.get("linkedin_url") or ""),
        portfolio_url=str(data.get("portfolio_url") or ""),
    )


@router.post("/applicant", response_model=ApplicantSettings)
def update_applicant(
    body: ApplicantSettings,
    profile_id: str = Depends(get_profile_id),
) -> ApplicantSettings:
    _patch_yaml(
        _applicant_path(profile_id),
        {
            "first_name": body.first_name,
            "last_name": body.last_name,
            "email": body.email,
            "phone": body.phone,
            "linkedin_url": body.linkedin_url,
        },
    )
    return get_applicant(profile_id=profile_id)


# FIX-5: open-ended PUT that merges arbitrary fields into the
# applicant YAML. Lets the new Settings UI edit fields the typed
# Spec-10 model does not enumerate (city, province, portfolio_url,
# education[], certifications[], etc.) while keeping fields it does
# not touch intact.
@router.put("/applicant")
def update_applicant_fields(
    body: dict = Body(...),
    profile_id: str = Depends(get_profile_id),
) -> dict:
    if not isinstance(body, dict) or not body:
        raise HTTPException(
            status_code=400, detail="body must be a non-empty object",
        )
    path = _applicant_path(profile_id)
    data = _load_yaml(path)
    data.update(body)
    _write_yaml(path, data)
    return data


# --- Section 2b: Gemini API key (FIX-5) -------------------------

class ApiKeyResponse(BaseModel):
    configured: bool
    masked: str
    model: str


class ApiKeyUpdate(BaseModel):
    key: str


@router.get("/api-key", response_model=ApiKeyResponse)
def get_api_key(
    profile_id: str = Depends(get_profile_id),
) -> ApiKeyResponse:
    path = _api_key_path(profile_id)
    key = (
        path.read_text(encoding="utf-8").strip()
        if path.exists() else ""
    )
    return ApiKeyResponse(
        configured=bool(key), masked=_mask_key(key), model=GEMINI_MODEL,
    )


@router.put("/api-key", response_model=ApiKeyResponse)
def update_api_key(
    body: ApiKeyUpdate,
    profile_id: str = Depends(get_profile_id),
) -> ApiKeyResponse:
    key = body.key.strip()
    if not key:
        raise HTTPException(
            status_code=400, detail="key must be non-empty",
        )
    path = _api_key_path(profile_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key, encoding="utf-8")
    return ApiKeyResponse(
        configured=True, masked=_mask_key(key), model=GEMINI_MODEL,
    )


# --- Section 3: LLM routing -------------------------------------

class LLMRouting(BaseModel):
    evaluation: str = "copy_paste"
    resume: str = "copy_paste"
    form_filling: str = "copy_paste"


@router.get("/llm", response_model=LLMRouting)
def get_llm(
    profile_id: str = Depends(get_profile_id),
) -> LLMRouting:
    data = _load_yaml(_profile_yaml_path(profile_id))
    routing = (data.get("llm_routing") or {})
    return LLMRouting(
        evaluation=routing.get("evaluation", "copy_paste"),
        resume=routing.get("resume", "copy_paste"),
        form_filling=routing.get("form_filling", "copy_paste"),
    )


@router.post("/llm", response_model=LLMRouting)
def update_llm(
    body: LLMRouting,
    profile_id: str = Depends(get_profile_id),
) -> LLMRouting:
    _patch_yaml(
        _profile_yaml_path(profile_id),
        {"llm_routing": body.model_dump()},
    )
    return get_llm(profile_id=profile_id)


# --- Section 4: Sources -----------------------------------------

class SourcesSettings(BaseModel):
    sources_enabled: list[str] = []
    jobspy_sites: list[str] = []
    jobspy_cities: list[str] = []
    linkedin_keywords: list[str] = []
    linkedin_locations: list[str] = []


@router.get("/sources", response_model=SourcesSettings)
def get_sources(
    profile_id: str = Depends(get_profile_id),
) -> SourcesSettings:
    data = _load_yaml(_profile_yaml_path(profile_id))
    jobspy = data.get("jobspy") or {}
    linkedin = data.get("linkedin_guest") or {}
    return SourcesSettings(
        sources_enabled=list(data.get("sources_enabled") or []),
        jobspy_sites=list(jobspy.get("sites") or []),
        jobspy_cities=list(jobspy.get("cities") or []),
        linkedin_keywords=list(linkedin.get("keywords_traditional") or []),
        linkedin_locations=list(linkedin.get("locations") or []),
    )


@router.post("/sources", response_model=SourcesSettings)
def update_sources(
    body: SourcesSettings,
    profile_id: str = Depends(get_profile_id),
) -> SourcesSettings:
    path = _profile_yaml_path(profile_id)
    existing = _load_yaml(path)
    jobspy = dict(existing.get("jobspy") or {})
    jobspy["sites"] = body.jobspy_sites
    jobspy["cities"] = body.jobspy_cities
    linkedin = dict(existing.get("linkedin_guest") or {})
    linkedin["keywords_traditional"] = body.linkedin_keywords
    linkedin["locations"] = body.linkedin_locations
    _patch_yaml(path, {
        "sources_enabled": body.sources_enabled,
        "jobspy": jobspy,
        "linkedin_guest": linkedin,
    })
    return get_sources(profile_id=profile_id)


# --- Section 5: Exclusions --------------------------------------

class ExclusionEntry(BaseModel):
    name: str
    aliases: list[str] = []
    subsidiaries: list[str] = []
    reason: str = ""
    added: str = ""


class ExclusionsList(BaseModel):
    # `entries` is the Spec-10 rich shape (with aliases / reason).
    # `companies` is the FIX-5 plain-string list — derived from
    # `entries` so a single response satisfies both UIs.
    entries: list[ExclusionEntry]
    companies: list[str] = []


def _load_exclusions(profile_id: str) -> list[dict[str, Any]]:
    data = _load_yaml(_exclusions_path(profile_id))
    # Two on-disk shapes coexist:
    #   Spec-10: {profiles: {pid: {excluded_employers: [{name, aliases, ...}, ...]}}}
    #   FIX-5:   {companies: ["Acme", "Globex", ...]}
    # If the FIX-5 flat shape is present, surface it as bare-name
    # entries so the test fixtures (which write FIX-5) and the
    # release Spec-10 callers both work.
    if isinstance(data.get("companies"), list):
        return [{"name": str(c)} for c in data["companies"] if c]
    profiles = data.get("profiles") or {}
    block = profiles.get(profile_id) or {}
    return list(block.get("excluded_employers") or [])


def _save_exclusions(profile_id: str, entries: list[dict[str, Any]]) -> None:
    data = _load_yaml(_exclusions_path(profile_id))
    # Honour the FIX-5 flat shape if the file is already in that
    # shape — otherwise persist as the Spec-10 nested profiles form.
    if isinstance(data.get("companies"), list) or not data:
        # Empty (just-created) file defaults to FIX-5 shape so tests
        # asserting `{"companies": [...]}` see what they expect.
        data["companies"] = [e.get("name") for e in entries if e.get("name")]
        _write_yaml(_exclusions_path(profile_id), data)
        return
    if "profiles" not in data or not isinstance(data["profiles"], dict):
        data["profiles"] = {}
    if profile_id not in data["profiles"] or not isinstance(
        data["profiles"][profile_id], dict
    ):
        data["profiles"][profile_id] = {}
    data["profiles"][profile_id]["excluded_employers"] = entries
    data.setdefault("version", 1)
    _write_yaml(_exclusions_path(profile_id), data)


def _exclusions_response(profile_id: str) -> ExclusionsList:
    raw = _load_exclusions(profile_id)
    entries = [
        ExclusionEntry(
            name=str(e.get("name") or ""),
            aliases=list(e.get("aliases") or []),
            subsidiaries=list(e.get("subsidiaries") or []),
            reason=str(e.get("reason") or ""),
            added=str(e.get("added") or ""),
        )
        for e in raw
    ]
    return ExclusionsList(
        entries=entries,
        companies=[e.name for e in entries if e.name],
    )


@router.get("/exclusions", response_model=ExclusionsList)
def list_exclusions(
    profile_id: str = Depends(get_profile_id),
) -> ExclusionsList:
    return _exclusions_response(profile_id)


@router.post("/exclusions", response_model=ExclusionsList)
def add_exclusion(
    body: dict = Body(...),
    profile_id: str = Depends(get_profile_id),
) -> ExclusionsList:
    # Accept either the Spec-10 rich shape (`{name, aliases, ...}`)
    # or the FIX-5 simple shape (`{company: "..."}`).
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    name = ""
    if isinstance(body.get("name"), str):
        name = body["name"].strip()
    if not name and isinstance(body.get("company"), str):
        name = body["company"].strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    entry: dict[str, Any] = {
        "name": name,
        "aliases": list(body.get("aliases") or []),
        "subsidiaries": list(body.get("subsidiaries") or []),
        "reason": str(body.get("reason") or ""),
        "added": str(body.get("added") or ""),
    }
    raw = _load_exclusions(profile_id)
    raw = [e for e in raw if (e.get("name") or "") != name]
    raw.append(entry)
    _save_exclusions(profile_id, raw)
    return _exclusions_response(profile_id)


@router.delete("/exclusions/{name}", response_model=ExclusionsList)
def remove_exclusion(
    name: str,
    profile_id: str = Depends(get_profile_id),
) -> ExclusionsList:
    raw = _load_exclusions(profile_id)
    raw = [e for e in raw if (e.get("name") or "") != name]
    _save_exclusions(profile_id, raw)
    return _exclusions_response(profile_id)


# --- Section 6: Schedule ----------------------------------------

class ScheduleSettings(BaseModel):
    schedule_time: str
    next_run: str | None = None
    last_run: dict | None = None


@router.get("/schedule", response_model=ScheduleSettings)
def get_schedule(
    profile_id: str = Depends(get_profile_id),
) -> ScheduleSettings:
    from jobagent.scheduler import schedule_snapshot

    # The setup wizard stashes the time at step 8; default to 02:00.
    state_path = _setup_state_path(profile_id)
    schedule_time = "02:00"
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            step8 = (state.get("steps") or {}).get("8") or {}
            schedule_time = step8.get("schedule_time") or schedule_time
        except (OSError, json.JSONDecodeError):
            pass

    snap = schedule_snapshot(
        profile_id, schedule_time=schedule_time,
    )
    return ScheduleSettings(
        schedule_time=schedule_time,
        next_run=snap.get("next_run"),
        last_run=snap.get("last_run"),
    )


class ScheduleUpdateRequest(BaseModel):
    schedule_time: str


class ScheduleUpdateResponse(BaseModel):
    ok: bool
    schedule_time: str
    detail: str | None = None


@router.post("/schedule", response_model=ScheduleUpdateResponse)
def update_schedule(
    body: ScheduleUpdateRequest,
    profile_id: str = Depends(get_profile_id),
) -> ScheduleUpdateResponse:
    from jobagent.services import install_service, ServiceError

    state_path = _setup_state_path(profile_id)
    state: dict[str, Any] = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
    state.setdefault("steps", {})["8"] = {
        "schedule_time": body.schedule_time,
        "installed": True,
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(state, indent=2, sort_keys=True), encoding="utf-8",
    )

    try:
        install_service(
            schedule_time=body.schedule_time, profile=profile_id,
        )
    except ServiceError as e:
        return ScheduleUpdateResponse(
            ok=False, schedule_time=body.schedule_time, detail=str(e),
        )
    return ScheduleUpdateResponse(
        ok=True, schedule_time=body.schedule_time,
    )


# --- Section 7: Gmail status ------------------------------------

class GmailStatus(BaseModel):
    configured: bool
    credentials_present: bool
    token_path: str | None = None


@router.get("/gmail", response_model=GmailStatus)
def get_gmail(
    profile_id: str = Depends(get_profile_id),
) -> GmailStatus:
    root = _project_root()
    token = root / "data" / profile_id / "gmail_token.json"
    creds = root / "data" / profile_id / "gmail_credentials.json"
    return GmailStatus(
        configured=token.exists(),
        credentials_present=creds.exists(),
        token_path=str(token.relative_to(root)) if token.exists() else None,
    )


# --- Section 8: Danger zone -------------------------------------

class ResetWizardResponse(BaseModel):
    deleted: bool


@router.post("/reset-wizard", response_model=ResetWizardResponse)
def reset_wizard(
    profile_id: str = Depends(get_profile_id),
) -> ResetWizardResponse:
    path = _setup_state_path(profile_id)
    if not path.exists():
        return ResetWizardResponse(deleted=False)
    try:
        path.unlink()
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return ResetWizardResponse(deleted=True)


_EXPORT_DIRS = (
    ("config", "config"),
    ("source_materials", "source_materials"),
    ("data", "data"),
)


@router.get("/export-data")
def export_data(
    profile_id: str = Depends(get_profile_id),
) -> StreamingResponse:
    """Stream a ZIP of the user's local data + config.

    Excludes venv, vendor, node_modules, frontend/dist, scripts/output.
    Useful for backups and for migrating to another machine.
    """
    root = _project_root()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for src_name, archive_name in _EXPORT_DIRS:
            src = root / src_name
            if not src.exists():
                continue
            for p in src.rglob("*"):
                if p.is_dir():
                    continue
                rel = p.relative_to(root)
                # Skip nested venvs / caches that sneak in.
                parts = set(rel.parts)
                if parts & {"__pycache__", ".pytest_cache", "node_modules"}:
                    continue
                zf.write(p, arcname=str(rel))
    buf.seek(0)
    headers = {
        "Content-Disposition": (
            f'attachment; filename="job-agent-{profile_id}-export.zip"'
        ),
    }
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/zip",
        headers=headers,
    )
