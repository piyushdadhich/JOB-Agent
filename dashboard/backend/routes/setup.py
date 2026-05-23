"""Spec 1 — onboarding wizard backend routes.

Persists wizard progress to `data/{profile}/setup_state.json`
so a half-finished wizard survives a page reload, a `job-agent
stop` / `start` cycle, or even a machine restart.

Endpoint surface (added incrementally across Spec 1's tasks):

  GET  /api/setup/status         — {completed, current_step}
  GET  /api/setup/hardware       — HardwareInfo from jobagent.platform
  POST /api/setup/step/{n}       — save step data, advance current_step
  GET  /api/setup/config         — full accumulated state
  POST /api/setup/complete       — force-mark done

  POST /api/setup/test-api-key   — validate provider key
  POST /api/setup/check-ollama   — local Ollama installed? models?
  POST /api/setup/pull-model     — `ollama pull <name>`, streaming

  POST /api/setup/profile        — write default.yaml + default_applicant.yaml
                                   from .example templates, populated
                                   with the user's name / email / phone
                                   / linkedin and the step 2 LLM
                                   routing. API keys go into the
                                   gitignored .env file, never into
                                   YAML.

  POST /api/setup/parse-resume   — multipart .docx upload; extracts
                                   role rows + raw text.
  GET  /api/setup/inventory-prompt — returns the inventory-builder
                                   prompt for copy-paste.
  POST /api/setup/parse-inventory-response — splits a markdown
                                   inventory response into roles +
                                   quality flags.
  POST /api/setup/save-inventory — writes career_inventory.md.
  GET  /api/setup/inventory-quality — quality stats for the saved
                                   inventory.

  POST /api/setup/target-market  — patches target_cities, salary,
                                   target_role_types, employer_size,
                                   industries into default.yaml.
  POST /api/setup/sources        — patches jobspy.cities + sites,
                                   linkedin_guest keywords,
                                   sources_enabled into default.yaml.
  POST /api/setup/gmail/upload   — credentials.json upload.
  POST /api/setup/gmail/finalize — checks gmail_token.json, marks
                                   gmail_configured in step state.

  POST /api/setup/install-service — schedule + service install
                                    (delegates to jobagent.services).
  POST /api/setup/first-run       — starts a discovery + eval run
                                    in a daemon thread.
  GET  /api/setup/first-run/progress — poll the latest progress dict.

State file shape:

  {
    "completed": bool,
    "current_step": int,        # 1..TOTAL_STEPS
    "steps": {                   # populated as the user progresses
      "1": {...},
      "2": {...}, ...
    }
  }
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

import requests
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from dashboard.backend.deps import get_profile_id

router = APIRouter(prefix="/api/setup", tags=["setup"])

TOTAL_STEPS = 8


# --- State file helpers ------------------------------------------

def _project_root() -> Path:
    # routes/ → backend/ → dashboard/ → repo root.
    return Path(__file__).resolve().parents[3]


def _state_path(profile_id: str) -> Path:
    return _project_root() / "data" / profile_id / "setup_state.json"


def _load_state(profile_id: str) -> dict[str, Any]:
    path = _state_path(profile_id)
    if not path.exists():
        return {"completed": False, "current_step": 1, "steps": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # Corrupt file: treat as empty so the wizard restarts.
        # Better UX than 500-ing on every page load.
        return {"completed": False, "current_step": 1, "steps": {}}
    # Defensive defaults in case the file pre-dates a field.
    data.setdefault("completed", False)
    data.setdefault("current_step", 1)
    data.setdefault("steps", {})
    return data


def _save_state(profile_id: str, state: dict[str, Any]) -> None:
    path = _state_path(profile_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state, indent=2, sort_keys=True),
        encoding="utf-8",
    )


# --- Response models ---------------------------------------------

class SetupStatus(BaseModel):
    completed: bool
    current_step: int
    total_steps: int


class HardwareSnapshot(BaseModel):
    os_name: str
    os_version: str
    cpu_model: str
    cpu_cores: int
    ram_total_gb: float
    ram_available_gb: float
    gpu_model: str | None = None
    gpu_vram_gb: float | None = None
    gpu_type: str | None = None
    disk_free_gb: float
    tier: str


class StepResponse(BaseModel):
    current_step: int
    completed: bool


class SetupConfig(BaseModel):
    completed: bool
    current_step: int
    total_steps: int
    steps: dict[str, Any]


# --- Endpoints ---------------------------------------------------

@router.get("/status", response_model=SetupStatus)
def get_status(
    profile_id: str = Depends(get_profile_id),
) -> SetupStatus:
    state = _load_state(profile_id)
    return SetupStatus(
        completed=state["completed"],
        current_step=int(state["current_step"]),
        total_steps=TOTAL_STEPS,
    )


@router.get("/hardware", response_model=HardwareSnapshot)
def get_hardware() -> HardwareSnapshot:
    # Lazy import: detect_hardware shells out to nvidia-smi etc.,
    # which can be slow on first call. Defer to request time so the
    # module import is cheap.
    from jobagent.platform import detect_hardware

    info = detect_hardware()
    return HardwareSnapshot(**asdict(info))


@router.post("/step/{step_n}", response_model=StepResponse)
def save_step(
    step_n: int,
    body: dict[str, Any] | None = None,
    profile_id: str = Depends(get_profile_id),
) -> StepResponse:
    if not (1 <= step_n <= TOTAL_STEPS):
        raise HTTPException(
            status_code=400,
            detail=(
                f"step_n must be 1..{TOTAL_STEPS}; got {step_n}"
            ),
        )
    state = _load_state(profile_id)
    state["steps"][str(step_n)] = body or {}
    # Advance current_step monotonically — the user can revisit a
    # finished step without rewinding the wizard cursor.
    state["current_step"] = max(state["current_step"], step_n + 1)
    if state["current_step"] > TOTAL_STEPS:
        state["completed"] = True
        state["current_step"] = TOTAL_STEPS
    _save_state(profile_id, state)
    return StepResponse(
        current_step=state["current_step"],
        completed=state["completed"],
    )


@router.get("/config", response_model=SetupConfig)
def get_config(
    profile_id: str = Depends(get_profile_id),
) -> SetupConfig:
    state = _load_state(profile_id)
    return SetupConfig(
        completed=state["completed"],
        current_step=int(state["current_step"]),
        total_steps=TOTAL_STEPS,
        steps=state["steps"],
    )


# --- Step 2 (LLM config): API-key validation ---------------------

class TestApiKeyRequest(BaseModel):
    provider: str   # "anthropic" | "openai" | "gemini"
    api_key: str


class TestApiKeyResponse(BaseModel):
    ok: bool
    detail: str | None = None


_KEY_TEST_TIMEOUT = 10


def _test_anthropic_key(key: str) -> tuple[bool, str | None]:
    try:
        r = requests.get(
            "https://api.anthropic.com/v1/models",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
            },
            timeout=_KEY_TEST_TIMEOUT,
        )
    except requests.RequestException as e:
        return False, f"network error: {e}"
    if r.status_code == 200:
        return True, None
    return False, f"HTTP {r.status_code}"


def _test_openai_key(key: str) -> tuple[bool, str | None]:
    try:
        r = requests.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=_KEY_TEST_TIMEOUT,
        )
    except requests.RequestException as e:
        return False, f"network error: {e}"
    if r.status_code == 200:
        return True, None
    return False, f"HTTP {r.status_code}"


def _test_gemini_key(key: str) -> tuple[bool, str | None]:
    try:
        r = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": key},
            timeout=_KEY_TEST_TIMEOUT,
        )
    except requests.RequestException as e:
        return False, f"network error: {e}"
    if r.status_code == 200:
        return True, None
    return False, f"HTTP {r.status_code}"


_PROVIDER_TESTERS = {
    "anthropic": _test_anthropic_key,
    "openai":    _test_openai_key,
    "gemini":    _test_gemini_key,
}


@router.post("/test-api-key", response_model=TestApiKeyResponse)
def test_api_key(body: TestApiKeyRequest) -> TestApiKeyResponse:
    tester = _PROVIDER_TESTERS.get(body.provider.lower())
    if tester is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown provider {body.provider!r}; "
                f"supported: {sorted(_PROVIDER_TESTERS)}"
            ),
        )
    if not body.api_key.strip():
        raise HTTPException(status_code=400, detail="api_key is empty")
    ok, detail = tester(body.api_key.strip())
    return TestApiKeyResponse(ok=ok, detail=detail)


# --- Step 2: Ollama install + models -----------------------------

class OllamaStatusResponse(BaseModel):
    installed: bool
    models: list[str]
    detail: str | None = None


def _list_ollama_models() -> tuple[bool, list[str], str | None]:
    if not shutil.which("ollama"):
        return False, [], "ollama binary not on PATH"
    try:
        out = subprocess.run(
            ["ollama", "list"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return True, [], f"ollama list failed: {e}"
    if out.returncode != 0:
        return True, [], out.stderr.strip() or out.stdout.strip()
    models: list[str] = []
    # `ollama list` outputs a header line + space-separated columns
    # per model: NAME ID SIZE MODIFIED. Take the first whitespace-
    # delimited token, skip the header.
    for line in out.stdout.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        models.append(line.split()[0])
    return True, models, None


@router.post("/check-ollama", response_model=OllamaStatusResponse)
def check_ollama() -> OllamaStatusResponse:
    installed, models, detail = _list_ollama_models()
    return OllamaStatusResponse(
        installed=installed, models=models, detail=detail,
    )


class PullModelRequest(BaseModel):
    model: str


def _stream_ollama_pull(model: str) -> Iterable[bytes]:
    """Yield raw stdout/stderr bytes from `ollama pull <model>`."""
    if not shutil.which("ollama"):
        yield b"ollama binary not on PATH\n"
        return
    proc = subprocess.Popen(
        ["ollama", "pull", model],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        text=False,
    )
    try:
        assert proc.stdout is not None
        for chunk in iter(lambda: proc.stdout.readline(), b""):
            yield chunk
    finally:
        proc.wait()
        if proc.returncode != 0:
            yield (
                f"\n[ollama pull exited {proc.returncode}]\n"
            ).encode("utf-8")


@router.post("/pull-model")
def pull_model(body: PullModelRequest) -> StreamingResponse:
    if not body.model.strip():
        raise HTTPException(status_code=400, detail="model is empty")
    return StreamingResponse(
        _stream_ollama_pull(body.model.strip()),
        media_type="text/plain",
    )


# --- Step 3 (profile creation) -----------------------------------

class ProfileRequest(BaseModel):
    full_name: str
    email: str
    phone: str = ""
    linkedin_url: str = ""
    # Optional API keys collected in Step 2 — written to the
    # gitignored .env file at repo root if provided.
    api_keys: dict[str, str] | None = None


class ProfileResponse(BaseModel):
    profile_yaml: str
    applicant_yaml: str
    env_updated: bool


_PROFILE_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai":    "OPENAI_API_KEY",
    "gemini":    "GEMINI_API_KEY",
}


def _split_name(full_name: str) -> tuple[str, str]:
    """Split 'Alex Doe' → ('Alex', 'Doe'). Single-token names get
    an empty surname rather than copying first into last."""
    parts = [p for p in full_name.strip().split() if p]
    if not parts:
        return ("", "")
    if len(parts) == 1:
        return (parts[0], "")
    return (parts[0], " ".join(parts[1:]))


def _write_yaml_dump(path: Path, data: dict[str, Any]) -> None:
    import yaml
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _append_env_keys(env_path: Path, api_keys: dict[str, str]) -> bool:
    """Append API keys to .env. Returns True if anything was written.

    Existing keys with the same name are NOT replaced — we append
    a new line per provided key. The user can hand-edit if they
    later want to rotate; this is the first-write path.
    """
    written = False
    lines: list[str] = []
    if env_path.exists():
        # Read existing env so we can dedupe — only append keys
        # that aren't already set.
        existing = set()
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in raw and not raw.strip().startswith("#"):
                existing.add(raw.split("=", 1)[0].strip())
    else:
        existing = set()
    for provider, key in api_keys.items():
        env_name = _PROFILE_KEY_ENV.get(provider.lower())
        if not env_name or not key.strip() or env_name in existing:
            continue
        lines.append(f"{env_name}={key.strip()}")
        written = True
    if lines:
        with env_path.open("a", encoding="utf-8") as f:
            if env_path.exists() and env_path.stat().st_size > 0:
                f.write("\n")
            f.write("\n".join(lines) + "\n")
    return written


@router.post("/profile", response_model=ProfileResponse)
def save_profile(
    body: ProfileRequest,
    profile_id: str = Depends(get_profile_id),
) -> ProfileResponse:
    import yaml

    if not body.full_name.strip():
        raise HTTPException(status_code=400, detail="full_name is required")
    if not body.email.strip():
        raise HTTPException(status_code=400, detail="email is required")

    root = _project_root()
    profile_template = root / "config" / "profiles" / "default.yaml.example"
    applicant_template = (
        root / "config" / "profiles" / "default_applicant.yaml.example"
    )
    profile_target = root / "config" / "profiles" / f"{profile_id}.yaml"
    applicant_target = (
        root / "config" / "profiles" / f"{profile_id}_applicant.yaml"
    )
    env_target = root / ".env"

    if not profile_template.exists():
        raise HTTPException(
            status_code=500,
            detail=f"missing template: {profile_template}",
        )
    if not applicant_template.exists():
        raise HTTPException(
            status_code=500,
            detail=f"missing template: {applicant_template}",
        )

    # --- Profile YAML: patch display_name + llm_routing -----------
    profile = yaml.safe_load(
        profile_template.read_text(encoding="utf-8"),
    ) or {}
    profile["display_name"] = body.full_name.strip()
    profile["profile_id"] = profile_id

    # Pull LLM routing out of the saved step 2 payload, if any.
    state = _load_state(profile_id)
    step2 = state.get("steps", {}).get("2") or {}
    routing = step2.get("routing") or {}
    if routing:
        profile["llm_routing"] = routing

    _write_yaml_dump(profile_target, profile)

    # --- Applicant YAML: patch identity fields --------------------
    applicant = yaml.safe_load(
        applicant_template.read_text(encoding="utf-8"),
    ) or {}
    first, last = _split_name(body.full_name)
    applicant.update({
        "first_name":   first,
        "last_name":    last,
        "email":        body.email.strip(),
        "phone":        body.phone.strip(),
        "linkedin_url": body.linkedin_url.strip(),
    })
    _write_yaml_dump(applicant_target, applicant)

    # --- .env: append API keys if any -----------------------------
    env_updated = False
    if body.api_keys:
        env_updated = _append_env_keys(env_target, body.api_keys)

    return ProfileResponse(
        profile_yaml=str(profile_target.relative_to(root)),
        applicant_yaml=str(applicant_target.relative_to(root)),
        env_updated=env_updated,
    )


# --- Step 4 (career inventory) -----------------------------------

# Heading pattern accepted by parse-inventory-response. Matches the
# style used by the .example template:
#   ### Title — Company — Dates
# Punctuation between Title/Company/Dates can be em-dash, en-dash,
# hyphen, or pipe. Whitespace tolerant.
_ROLE_HEADING_RE = re.compile(
    r"^###\s+(?P<title>.+?)\s*[—–\-|]\s*(?P<company>.+?)\s*[—–\-|]\s*(?P<dates>.+?)\s*$",
)


class ResumeParseResponse(BaseModel):
    roles: list[dict]
    raw_text: str
    parser: str       # "docx" | "txt"


def _parse_resume_docx(content: bytes) -> tuple[list[dict], str]:
    from io import BytesIO
    try:
        from docx import Document
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="python-docx not installed",
        )
    doc = Document(BytesIO(content))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    raw = "\n".join(paragraphs)
    # Very loose role detection: any line that contains "—", "–",
    # or " - " between two non-empty tokens with the second-half
    # containing a 4-digit year. This catches "Senior PM — Acme,
    # 2019-2023" style lines without committing to a strict format.
    roles: list[dict] = []
    year_re = re.compile(r"\b(19|20)\d{2}\b")
    sep_re = re.compile(r"\s*[—–\-|]\s*")
    for line in paragraphs:
        if not year_re.search(line):
            continue
        parts = sep_re.split(line, maxsplit=2)
        if len(parts) >= 3:
            roles.append({
                "title": parts[0].strip(),
                "company": parts[1].strip(),
                "dates": parts[2].strip(),
            })
    return roles, raw


@router.post("/parse-resume", response_model=ResumeParseResponse)
async def parse_resume(
    file: UploadFile = File(...),
) -> ResumeParseResponse:
    content = await file.read()
    name = (file.filename or "").lower()
    if name.endswith(".docx"):
        roles, raw = _parse_resume_docx(content)
        parser = "docx"
    elif name.endswith(".txt") or name.endswith(".md"):
        raw = content.decode("utf-8", errors="replace")
        roles = []
        parser = "txt"
    else:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unsupported resume format {name!r}; "
                f"expected .docx, .txt, or .md"
            ),
        )
    return ResumeParseResponse(roles=roles, raw_text=raw, parser=parser)


# --- Inventory-builder prompt ------------------------------------

_INVENTORY_PROMPT = """\
You are a career inventory builder. Your job is to conduct a
structured interview to build a comprehensive record of each
professional role I've held. This is NOT a resume — it is the
complete, detailed record from which resumes will be generated
later. Detail and honesty matter more than concision.

For each role, ask about:
  1. Exact dates, title, company, location.
  2. Team size, who I reported to, who reported to me.
  3. What I ACTUALLY did daily (NOT resume bullet points).
  4. Tools / technologies used daily.
  5. Measurable outcomes (numbers, percentages, dollar amounts).
  6. Why I left.
  7. What I would say in an interview about this role.
  8. Leadership moments and judgment calls.

Keep asking until each role has comprehensive detail. When the
interview is done, output the inventory in this exact markdown
format (one block per role, oldest first):

  # Career Inventory — {{display_name}}

  ## Roles

  ### {{Title}} — {{Company}} — {{Start}} to {{End}}
  **Location:** ...
  **Team size:** ...
  **Reported to:** ...

  ...daily work narrative (2-3 paragraphs)...

  **Tools / technologies daily:**
  - ...

  **Measurable outcomes:**
  - ...

  **Why I left:** ...

  **Interview narrative:** ...

Continue with the next role below the divider until every role
is documented.
"""


class InventoryPromptResponse(BaseModel):
    prompt: str


@router.get("/inventory-prompt", response_model=InventoryPromptResponse)
def get_inventory_prompt(
    profile_id: str = Depends(get_profile_id),
) -> InventoryPromptResponse:
    state = _load_state(profile_id)
    display_name = (
        state.get("steps", {}).get("3", {}).get("full_name")
        or "the applicant"
    )
    return InventoryPromptResponse(
        prompt=_INVENTORY_PROMPT.replace("{{display_name}}", display_name),
    )


# --- Inventory response parser + quality scoring -----------------

class ParsedRole(BaseModel):
    title: str
    company: str
    dates: str
    body: str
    body_chars: int


class InventoryParseRequest(BaseModel):
    markdown: str


class InventoryParseResponse(BaseModel):
    roles: list[ParsedRole]
    quality_score: int        # 0..100
    quality_notes: list[str]


def _parse_inventory_markdown(text: str) -> list[ParsedRole]:
    """Split the markdown into role blocks keyed by ### headings.

    Tolerant of leading/trailing whitespace and stray content
    between roles. Anything before the first ### heading is
    discarded (typically the H1 + "## Roles" preface).
    """
    roles: list[ParsedRole] = []
    current_match: re.Match[str] | None = None
    body_lines: list[str] = []

    def _flush() -> None:
        if current_match is None:
            return
        body = "\n".join(body_lines).strip()
        roles.append(ParsedRole(
            title=current_match.group("title").strip(),
            company=current_match.group("company").strip(),
            dates=current_match.group("dates").strip(),
            body=body,
            body_chars=len(body),
        ))

    for line in text.splitlines():
        m = _ROLE_HEADING_RE.match(line)
        if m is not None:
            _flush()
            current_match = m
            body_lines = []
        elif current_match is not None:
            body_lines.append(line)
    _flush()
    return roles


def _score_inventory(roles: list[ParsedRole]) -> tuple[int, list[str]]:
    """Quality score 0-100 + a list of human-readable notes."""
    notes: list[str] = []
    if not roles:
        return 0, ["No roles detected. Did you paste the full response?"]

    # Each role contributes up to 25 chars/sec ish — cap to 4 roles
    # for scoring; deeper inventories don't add headline quality.
    score = 0
    role_cap = min(4, len(roles))
    per_role = 100 // role_cap
    thin_roles: list[str] = []
    for role in roles[:role_cap]:
        if role.body_chars >= 1500:
            score += per_role
        elif role.body_chars >= 600:
            score += per_role * 2 // 3
        elif role.body_chars >= 200:
            score += per_role // 2
            thin_roles.append(role.title)
        else:
            score += per_role // 5
            thin_roles.append(role.title)
    if thin_roles:
        notes.append(
            "Roles with thin detail: " + ", ".join(thin_roles)
            + ". Each role should have 600+ chars of substance "
            "(daily work, tools, outcomes, narrative)."
        )
    if len(roles) < 3:
        notes.append(
            f"Only {len(roles)} role(s) detected. Most users have "
            "3+ — add early-career roles even if brief."
        )
    return min(score, 100), notes


@router.post(
    "/parse-inventory-response", response_model=InventoryParseResponse,
)
def parse_inventory_response(
    body: InventoryParseRequest,
) -> InventoryParseResponse:
    roles = _parse_inventory_markdown(body.markdown)
    score, notes = _score_inventory(roles)
    return InventoryParseResponse(
        roles=roles, quality_score=score, quality_notes=notes,
    )


class SaveInventoryRequest(BaseModel):
    markdown: str


class SaveInventoryResponse(BaseModel):
    path: str
    bytes_written: int


@router.post("/save-inventory", response_model=SaveInventoryResponse)
def save_inventory(
    body: SaveInventoryRequest,
    profile_id: str = Depends(get_profile_id),
) -> SaveInventoryResponse:
    if not body.markdown.strip():
        raise HTTPException(status_code=400, detail="markdown is empty")
    root = _project_root()
    target = root / "source_materials" / profile_id / "career_inventory.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body.markdown, encoding="utf-8")
    return SaveInventoryResponse(
        path=str(target.relative_to(root)),
        bytes_written=len(body.markdown.encode("utf-8")),
    )


class InventoryQualityResponse(BaseModel):
    exists: bool
    role_count: int
    quality_score: int
    quality_notes: list[str]


@router.get("/inventory-quality", response_model=InventoryQualityResponse)
def get_inventory_quality(
    profile_id: str = Depends(get_profile_id),
) -> InventoryQualityResponse:
    root = _project_root()
    path = root / "source_materials" / profile_id / "career_inventory.md"
    if not path.exists():
        return InventoryQualityResponse(
            exists=False, role_count=0, quality_score=0,
            quality_notes=["No career_inventory.md saved yet."],
        )
    roles = _parse_inventory_markdown(path.read_text(encoding="utf-8"))
    score, notes = _score_inventory(roles)
    return InventoryQualityResponse(
        exists=True,
        role_count=len(roles),
        quality_score=score,
        quality_notes=notes,
    )


# --- Profile YAML patcher (Steps 5 + 6) --------------------------

def _patch_profile_yaml(profile_id: str, updates: dict[str, Any]) -> Path:
    """Merge `updates` into config/profiles/{profile_id}.yaml.

    Existing keys are overwritten; missing keys are added. The
    file is created from default.yaml.example if it doesn't exist
    yet (covers users who skipped Step 3 or who hand-deleted the
    file). Raises HTTPException(500) if neither file nor template
    is available.
    """
    import yaml

    root = _project_root()
    target = root / "config" / "profiles" / f"{profile_id}.yaml"
    template = root / "config" / "profiles" / "default.yaml.example"

    if target.exists():
        data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    elif template.exists():
        data = yaml.safe_load(template.read_text(encoding="utf-8")) or {}
        data["profile_id"] = profile_id
    else:
        raise HTTPException(
            status_code=500,
            detail="profile YAML and template both missing",
        )

    for key, value in updates.items():
        data[key] = value

    _write_yaml_dump(target, data)
    return target


# --- Step 5 (target market) --------------------------------------

class SalarySpec(BaseModel):
    floor: int = 0
    target: int = 0
    cap: int = 0


class TargetMarketRequest(BaseModel):
    target_cities: list[str] = []
    remote_preference: str | None = None       # "remote" | "hybrid" | "onsite"
    salary: SalarySpec = SalarySpec()
    target_role_types: list[str] = []
    employer_size_priority: str | None = None  # "small" | "mid_sized" | "large"
    industries: list[str] = []


class TargetMarketResponse(BaseModel):
    profile_yaml: str


@router.post("/target-market", response_model=TargetMarketResponse)
def save_target_market(
    body: TargetMarketRequest,
    profile_id: str = Depends(get_profile_id),
) -> TargetMarketResponse:
    updates: dict[str, Any] = {}
    if body.target_cities:
        updates["target_cities"] = body.target_cities
    if body.target_role_types:
        updates["target_role_types"] = body.target_role_types
    if body.employer_size_priority:
        updates["employer_size_priority"] = body.employer_size_priority
    if body.industries:
        updates["target_industries"] = body.industries
    if body.remote_preference:
        updates["remote_preference"] = body.remote_preference
    if body.salary.floor or body.salary.target or body.salary.cap:
        updates["salary"] = {
            "floor":  body.salary.floor,
            "target": body.salary.target,
            "cap":    body.salary.cap,
        }
    target = _patch_profile_yaml(profile_id, updates)
    root = _project_root()
    return TargetMarketResponse(
        profile_yaml=str(target.relative_to(root)),
    )


# --- Step 6 (sources) -------------------------------------------

class SourcesRequest(BaseModel):
    sources_enabled: list[str] = []
    country: str = "Canada"
    jobspy_sites: list[str] = ["indeed", "google"]
    jobspy_cities: list[str] = []
    linkedin_keywords: list[str] = []
    linkedin_locations: list[str] = []


@router.post("/sources", response_model=TargetMarketResponse)
def save_sources(
    body: SourcesRequest,
    profile_id: str = Depends(get_profile_id),
) -> TargetMarketResponse:
    updates: dict[str, Any] = {}
    if body.sources_enabled:
        updates["sources_enabled"] = body.sources_enabled
    if body.country:
        updates["country"] = body.country

    # jobspy + linkedin_guest are sub-dicts; we merge into them
    # rather than overwriting the whole config block (which would
    # wipe rate-limit knobs etc.).
    import yaml
    root = _project_root()
    target = root / "config" / "profiles" / f"{profile_id}.yaml"
    template = root / "config" / "profiles" / "default.yaml.example"
    src = target if target.exists() else template
    existing = (
        yaml.safe_load(src.read_text(encoding="utf-8"))
        if src.exists() else {}
    ) or {}

    jobspy = dict(existing.get("jobspy") or {})
    if body.jobspy_sites:
        jobspy["sites"] = body.jobspy_sites
    if body.jobspy_cities:
        jobspy["cities"] = body.jobspy_cities
    if jobspy:
        updates["jobspy"] = jobspy

    linkedin = dict(existing.get("linkedin_guest") or {})
    if body.linkedin_keywords:
        linkedin["keywords_traditional"] = body.linkedin_keywords
    if body.linkedin_locations:
        linkedin["locations"] = body.linkedin_locations
    if linkedin:
        updates["linkedin_guest"] = linkedin

    written = _patch_profile_yaml(profile_id, updates)
    return TargetMarketResponse(
        profile_yaml=str(written.relative_to(root)),
    )


# --- Step 7 (Gmail — optional) ----------------------------------

class GmailUploadResponse(BaseModel):
    saved_to: str


@router.post("/gmail/upload", response_model=GmailUploadResponse)
async def gmail_upload(
    file: UploadFile = File(...),
    profile_id: str = Depends(get_profile_id),
) -> GmailUploadResponse:
    if not (file.filename or "").lower().endswith(".json"):
        raise HTTPException(
            status_code=400,
            detail="credentials must be a .json file",
        )
    content = await file.read()
    # Sanity-check it's valid JSON before persisting.
    try:
        json.loads(content)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=400, detail=f"invalid JSON: {e}",
        )
    root = _project_root()
    target = root / "data" / profile_id / "gmail_credentials.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return GmailUploadResponse(saved_to=str(target.relative_to(root)))


class GmailFinalizeResponse(BaseModel):
    gmail_configured: bool
    token_path: str | None = None


@router.post("/gmail/finalize", response_model=GmailFinalizeResponse)
def gmail_finalize(
    profile_id: str = Depends(get_profile_id),
) -> GmailFinalizeResponse:
    """Called after the user runs `job-agent gmail-auth` in a
    terminal. We just check if the resulting token file exists."""
    root = _project_root()
    token = root / "data" / profile_id / "gmail_token.json"
    state = _load_state(profile_id)
    if token.exists():
        # Patch into setup state so other tabs (Email Monitor
        # panel on Shortlist) can read gmail_configured from
        # /api/setup/config without re-checking the filesystem.
        state.setdefault("steps", {})["7"] = {"gmail_configured": True}
        _save_state(profile_id, state)
        return GmailFinalizeResponse(
            gmail_configured=True,
            token_path=str(token.relative_to(root)),
        )
    state.setdefault("steps", {})["7"] = {"gmail_configured": False}
    _save_state(profile_id, state)
    return GmailFinalizeResponse(gmail_configured=False)


# --- Step 8 (schedule + service install) -------------------------

class InstallServiceRequest(BaseModel):
    schedule_time: str = "02:00"


class InstallServiceResponse(BaseModel):
    ok: bool
    detail: str | None = None


@router.post("/install-service", response_model=InstallServiceResponse)
def install_service_endpoint(
    body: InstallServiceRequest,
    profile_id: str = Depends(get_profile_id),
) -> InstallServiceResponse:
    from jobagent.services import install_service, ServiceError

    try:
        install_service(
            schedule_time=body.schedule_time, profile=profile_id,
        )
    except ServiceError as e:
        return InstallServiceResponse(ok=False, detail=str(e))
    return InstallServiceResponse(ok=True)


# --- Step 9 (first discovery run + streaming progress) -----------

import threading

# In-memory progress map keyed by profile. A real production
# system would persist this; for the onboarding wizard's lifetime
# it's fine — we only need progress while the user has the page open.
_FIRST_RUN_STATE: dict[str, dict[str, Any]] = {}
_FIRST_RUN_LOCK = threading.Lock()


def _set_first_run(profile_id: str, **fields: Any) -> None:
    with _FIRST_RUN_LOCK:
        cur = _FIRST_RUN_STATE.setdefault(
            profile_id,
            {"status": "idle", "stages": [], "summary": None},
        )
        cur.update(fields)


def _first_run_snapshot(profile_id: str) -> dict[str, Any]:
    with _FIRST_RUN_LOCK:
        return dict(
            _FIRST_RUN_STATE.get(
                profile_id,
                {"status": "idle", "stages": [], "summary": None},
            )
        )


def _run_first_pipeline(profile_id: str) -> None:
    """Background thread target — invokes scripts/run_daily.py and
    streams a coarse stage timeline into _FIRST_RUN_STATE."""
    _set_first_run(profile_id, status="running", stages=[
        {"name": "discovery", "state": "in_progress"},
    ])
    try:
        # Lazy import — scripts.run_daily.main is heavy.
        from scripts import run_daily  # type: ignore

        rc = run_daily.main(["--profile", profile_id])
        if rc == 0:
            _set_first_run(
                profile_id, status="done", summary={
                    "rc": 0, "message": "First run complete.",
                },
                stages=[
                    {"name": "discovery", "state": "done"},
                    {"name": "evaluation", "state": "done"},
                ],
            )
        else:
            _set_first_run(
                profile_id, status="failed", summary={
                    "rc": rc, "message": f"run_daily exited {rc}",
                },
            )
    except Exception as e:  # pragma: no cover — defensive
        _set_first_run(
            profile_id, status="failed", summary={
                "rc": -1, "message": f"{type(e).__name__}: {e}",
            },
        )


@router.post("/first-run", response_model=dict)
def start_first_run(
    profile_id: str = Depends(get_profile_id),
) -> dict[str, Any]:
    snap = _first_run_snapshot(profile_id)
    if snap.get("status") == "running":
        return {"started": False, "reason": "already running"}
    _set_first_run(
        profile_id, status="running", stages=[
            {"name": "starting", "state": "in_progress"},
        ], summary=None,
    )
    t = threading.Thread(
        target=_run_first_pipeline, args=(profile_id,), daemon=True,
    )
    t.start()
    return {"started": True}


@router.get("/first-run/progress", response_model=dict)
def get_first_run_progress(
    profile_id: str = Depends(get_profile_id),
) -> dict[str, Any]:
    return _first_run_snapshot(profile_id)


# --- /complete ---------------------------------------------------

@router.post("/complete", response_model=SetupStatus)
def mark_complete(
    profile_id: str = Depends(get_profile_id),
) -> SetupStatus:
    """Force-mark setup as complete (used by TASK 8 service-install
    step, and as an escape hatch when a user manually finishes
    config outside the wizard)."""
    state = _load_state(profile_id)
    state["completed"] = True
    state["current_step"] = TOTAL_STEPS
    _save_state(profile_id, state)
    return SetupStatus(
        completed=True,
        current_step=TOTAL_STEPS,
        total_steps=TOTAL_STEPS,
    )
