"""v2.3 daily pipeline: discover -> evaluate -> shortlist.

Usage:
  python scripts/run_daily.py --profile default
  python scripts/run_daily.py --profile default --skip-discovery
  python scripts/run_daily.py --profile default --skip-eval
  python scripts/run_daily.py --profile default --limit 10 --dry-run

Phase 1 (discovery): iterates sources_enabled from
config/profiles/{profile}.yaml. Sources with implemented handlers
(greenhouse_api, jobspy, linkedin_guest) run; others log
'not implemented' and continue. Source-level errors are caught and
logged; the pipeline never aborts on a single source failure.

Phase 2 (evaluate): queries the tracker for postings with no
eval_decision OR a stale evaluator_version, then runs the v2.3
two-phase pipeline (Stage 2a + Stage 2pre on Gemma 3 4B, then
Stage 2c-score + 2c-counter on Gemma 4 E4B). Persists each verdict
to eval_decisions + stage_decisions. Pre-flight checks Ollama
availability and that both filter and decide models are present.

Phase 3 (shortlist): selects STRONG and TOP_TIER from the latest
pipeline-v2.3.0 decisions, sorts by fit_score DESC, writes
scripts/output/shortlist_{YYYY-MM-DD}.md, and prints the head to
stdout.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.activity_log import log_activity
from engine.persistence.tracker import Tracker
from llm.client import LLMClient
from skills.inventory import InventoryTool
from skills.role_evaluator.pipeline import (
    DECIDE_MODEL,
    FILTER_MODEL,
    PipelineVerdict,
    run_batch,
)
from skills.role_evaluator.profile_config import ProfileConfig
from skills.role_evaluator.stage2a import Stage2a
from scripts.run_full_eval_v2_3 import (
    CURRENT_EVALUATOR_VERSIONS,
    EVALUATOR_VERSION,
    FALLBACK_VERSION,
    persist_verdict,
)

logger = logging.getLogger(__name__)
OUTPUT_DIR = PROJECT_ROOT / "scripts" / "output"
OLLAMA_HOST = "http://localhost:11434"


class TeeWriter:
    """Duplicate writes to the underlying stdout AND a log file.

    Used so unattended Task Scheduler runs leave a full audit trail
    on disk (print() output is otherwise lost when stdout is detached).
    """

    def __init__(self, log_path: Path):
        self._log = open(log_path, "a", encoding="utf-8")
        self._stdout = sys.stdout

    def write(self, text):
        self._stdout.write(text)
        self._log.write(text)

    def flush(self):
        self._stdout.flush()
        self._log.flush()

    def close(self):
        self._log.close()


# --- Profile config -------------------------------------------------

def _profile_yaml_path(profile_id: str) -> Path:
    return PROJECT_ROOT / "config" / "profiles" / f"{profile_id}.yaml"


def load_profile(profile_id: str) -> dict:
    path = _profile_yaml_path(profile_id)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# --- Ollama pre-flight ---------------------------------------------

def check_ollama_available(host: str = OLLAMA_HOST) -> bool:
    try:
        r = requests.get(f"{host}/api/version", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def get_available_models(host: str = OLLAMA_HOST) -> set[str]:
    try:
        r = requests.get(f"{host}/api/tags", timeout=5)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return set()
    names: set[str] = set()
    for m in data.get("models", []):
        n = m.get("name") or m.get("model") or ""
        if n:
            names.add(n)
            names.add(n.split(":")[0])
    return names


def check_model_present(model: str, host: str = OLLAMA_HOST) -> bool:
    available = get_available_models(host)
    if model in available:
        return True
    return model.split(":")[0] in available


# --- Discovery handlers --------------------------------------------

def _run_greenhouse(
    profile_id: str, tracker, llm,
    dry_run: bool, limit: Optional[int],
    focus: Optional[str] = None,
) -> dict:
    """Greenhouse handler: v2 API client.

    Hits engine.discovery.greenhouse_client.GreenhouseClient against the
    public board API; persists via persist_record; stamps
    source='greenhouse_api'. Slugs come from
    config/profiles/<profile>.yaml under greenhouse_api.

    v1 HTML scraper removed in Spec 6a TASK 3 (May 2026); see commits
    396ff24..HEAD for the migration history.
    """
    from engine.discovery.greenhouse_client import GreenhouseClient

    profile = load_profile(profile_id)
    v2_cfg = profile.get("greenhouse_api", {}) or {}

    slugs = v2_cfg.get("slugs") or []
    if not slugs:
        v2_counts = {"new": 0, "dup": 0, "skipped": 0, "error": 0}
        print(
            "    greenhouse v2 (api): skipped "
            "(greenhouse_api.slugs is empty in profile yaml)"
        )
    else:
        client = GreenhouseClient(
            slugs=slugs,
            rate_limit=v2_cfg.get("rate_limit_seconds", 1.0),
        )
        v2_counts = _persist_records(client, tracker, dry_run, limit)
        print(
            f"    greenhouse v2 (api): "
            f"slugs={len(slugs)} "
            f"new={v2_counts['new']} dup={v2_counts['dup']} "
            f"skipped={v2_counts['skipped']} error={v2_counts['error']}"
        )

    return v2_counts


def _persist_records(
    client, tracker, dry_run: bool, limit: Optional[int],
) -> dict:
    """Iterate client.fetch() and persist each record.

    Shared by all source handlers. The client is any object exposing
    `fetch() -> Iterator[OpportunityRecord]`. dry_run counts what
    would have been persisted but does not write to the tracker.
    Returns counts dict {new, dup, skipped, error}.
    """
    from engine.persistence.opportunities import (
        IncompleteRecordError,
        persist_record,
    )

    new_count = 0
    dup_count = 0
    skipped = 0
    errors = 0
    for record in client.fetch():
        if limit is not None and (new_count + dup_count) >= limit:
            break
        try:
            if dry_run:
                new_count += 1
                continue
            _, was_new = persist_record(tracker, record)
            if was_new:
                new_count += 1
            else:
                dup_count += 1
        except IncompleteRecordError:
            skipped += 1
        except Exception:
            logger.exception("persist_record failed")
            errors += 1
    return {
        "new": new_count, "dup": dup_count,
        "skipped": skipped, "error": errors,
    }


AI_ROLE_TYPE_ID = "ai_roles"


def _filter_role_types_for_focus(profile, focus: Optional[str]):
    """Return profile with target_role_types filtered by focus.

    focus="ai" keeps only ai_roles; focus="traditional" drops it;
    focus=None preserves the full list. Uses dataclasses.replace
    so the loader's cached Profile is not mutated.
    """
    if focus is None:
        return profile
    from dataclasses import replace
    if focus == "ai":
        kept = [
            rt for rt in profile.target_role_types
            if rt.id == AI_ROLE_TYPE_ID
        ]
    else:  # "traditional"
        kept = [
            rt for rt in profile.target_role_types
            if rt.id != AI_ROLE_TYPE_ID
        ]
    return replace(profile, target_role_types=kept)


def _resolve_linkedin_keywords(
    linkedin_cfg: dict, focus: Optional[str],
) -> list[str]:
    """Pick LinkedIn keywords based on focus.

    Honors the keywords_traditional/keywords_ai split when present.
    Falls back to a legacy flat `keywords:` list for backward
    compat — in that case all flat keywords are treated as
    traditional, so --focus ai returns [].
    """
    kw_traditional = list(linkedin_cfg.get("keywords_traditional") or [])
    kw_ai = list(linkedin_cfg.get("keywords_ai") or [])
    legacy_flat = list(linkedin_cfg.get("keywords") or [])
    has_split = bool(kw_traditional or kw_ai)
    if focus == "ai":
        return kw_ai if has_split else []
    if focus == "traditional":
        return kw_traditional if has_split else legacy_flat
    if has_split:
        return kw_traditional + kw_ai
    return legacy_flat


def _run_jobspy(
    profile_id: str, tracker, llm,
    dry_run: bool, limit: Optional[int],
    focus: Optional[str] = None,
) -> dict:
    from engine.discovery.jobspy_client import JobSpyClient
    from engine.profiles.loader import load_profile_from_default

    profile = load_profile_from_default(profile_id)
    profile = _filter_role_types_for_focus(profile, focus)
    if not profile.target_role_types:
        return {"new": 0, "dup": 0, "skipped": 0, "error": 0}
    client = JobSpyClient(profile=profile)
    return _persist_records(client, tracker, dry_run, limit)


def _run_linkedin_guest(
    profile_id: str, tracker, llm,
    dry_run: bool, limit: Optional[int],
    focus: Optional[str] = None,
) -> dict:
    from engine.discovery.linkedin_guest import LinkedInGuestClient
    from engine.profiles.loader import load_profile_from_default

    profile = load_profile_from_default(profile_id)
    linkedin_cfg = dict(
        (profile.source_config or {}).get("linkedin_guest") or {}
    )
    keywords = _resolve_linkedin_keywords(linkedin_cfg, focus)
    if not keywords:
        return {"new": 0, "dup": 0, "skipped": 0, "error": 0}
    linkedin_cfg["keywords"] = keywords
    profile.source_config["linkedin_guest"] = linkedin_cfg
    client = LinkedInGuestClient(profile=profile)
    return _persist_records(client, tracker, dry_run, limit)


def _run_lever(
    profile_id: str, tracker, llm,
    dry_run: bool, limit: Optional[int],
    focus: Optional[str] = None,
) -> dict:
    from engine.discovery.lever_client import LeverClient

    profile = load_profile(profile_id)
    cfg = profile.get("lever_api", {}) or {}
    slugs = cfg.get("slugs") or []
    if not slugs:
        return {"new": 0, "dup": 0, "skipped": 0, "error": 0}
    client = LeverClient(
        slugs=slugs,
        rate_limit=cfg.get("rate_limit_seconds", 1.0),
    )
    return _persist_records(client, tracker, dry_run, limit)


def _run_ashby(
    profile_id: str, tracker, llm,
    dry_run: bool, limit: Optional[int],
    focus: Optional[str] = None,
) -> dict:
    from engine.discovery.ashby_client import AshbyClient

    profile = load_profile(profile_id)
    cfg = profile.get("ashby_api", {}) or {}
    slugs = cfg.get("slugs") or []
    if not slugs:
        return {"new": 0, "dup": 0, "skipped": 0, "error": 0}
    client = AshbyClient(
        slugs=slugs,
        rate_limit=cfg.get("rate_limit_seconds", 1.0),
        include_compensation=cfg.get("include_compensation", True),
    )
    return _persist_records(client, tracker, dry_run, limit)


def _run_workable(
    profile_id: str, tracker, llm,
    dry_run: bool, limit: Optional[int],
    focus: Optional[str] = None,
) -> dict:
    from engine.discovery.workable_client import WorkableClient

    profile = load_profile(profile_id)
    cfg = profile.get("workable_api", {}) or {}
    slugs = cfg.get("slugs") or []
    if not slugs:
        return {"new": 0, "dup": 0, "skipped": 0, "error": 0}
    client = WorkableClient(
        slugs=slugs,
        rate_limit=cfg.get("rate_limit_seconds", 1.0),
        fetch_details=cfg.get("fetch_details", True),
    )
    return _persist_records(client, tracker, dry_run, limit)


def _run_personio(
    profile_id: str, tracker, llm,
    dry_run: bool, limit: Optional[int],
    focus: Optional[str] = None,
) -> dict:
    from engine.discovery.personio_client import PersonioClient

    profile = load_profile(profile_id)
    cfg = profile.get("personio_api", {}) or {}
    slugs = cfg.get("slugs") or []
    if not slugs:
        return {"new": 0, "dup": 0, "skipped": 0, "error": 0}
    client = PersonioClient(
        slugs=slugs,
        rate_limit=cfg.get("rate_limit_seconds", 1.0),
    )
    return _persist_records(client, tracker, dry_run, limit)


def _run_recruitee(
    profile_id: str, tracker, llm,
    dry_run: bool, limit: Optional[int],
    focus: Optional[str] = None,
) -> dict:
    from engine.discovery.recruitee_client import RecruiteeClient

    profile = load_profile(profile_id)
    cfg = profile.get("recruitee_api", {}) or {}
    slugs = cfg.get("slugs") or []
    if not slugs:
        return {"new": 0, "dup": 0, "skipped": 0, "error": 0}
    client = RecruiteeClient(
        slugs=slugs,
        rate_limit=cfg.get("rate_limit_seconds", 1.0),
    )
    return _persist_records(client, tracker, dry_run, limit)


def _run_workday(
    profile_id: str, tracker, llm,
    dry_run: bool, limit: Optional[int],
    focus: Optional[str] = None,
) -> dict:
    from engine.discovery.workday_client import WorkdayClient

    profile = load_profile(profile_id)
    cfg = profile.get("workday", {}) or {}
    tenants = cfg.get("tenants") or []
    if not tenants:
        return {"new": 0, "dup": 0, "skipped": 0, "error": 0}
    client = WorkdayClient(
        tenants=tenants,
        rate_limit=cfg.get("rate_limit_seconds", 2.0),
        fetch_details=cfg.get("fetch_details", True),
    )
    return _persist_records(client, tracker, dry_run, limit)


def _run_email_monitor(
    profile_id: str, tracker, llm,
    dry_run: bool, limit: Optional[int],
    focus: Optional[str] = None,
) -> dict:
    try:
        from engine.discovery.email_monitor import EmailMonitorClient
    except ImportError as e:
        logger.warning(
            "Email monitor: SDK not installed (%s); skipping", e,
        )
        return {"new": 0, "dup": 0, "skipped": 0, "error": 0}

    profile = load_profile(profile_id)
    cfg = profile.get("email_monitor", {}) or {}
    client = EmailMonitorClient(
        profile_id=profile_id,
        hours_lookback=cfg.get("hours_lookback", 24),
        recruiter_domains=cfg.get("recruiter_domains") or [],
        newsletter_senders=cfg.get("newsletter_senders") or [],
        search_query_override=cfg.get("search_query_override"),
    )
    return _persist_records(client, tracker, dry_run, limit)


def _run_job_bank_csv(
    profile_id: str, tracker, llm,
    dry_run: bool, limit: Optional[int],
    focus: Optional[str] = None,
) -> dict:
    from engine.discovery.job_bank_csv import JobBankCSVClient

    profile = load_profile(profile_id)
    cfg = profile.get("job_bank_csv", {}) or {}
    csv_path = cfg.get("csv_path") or ""
    if not csv_path:
        return {"new": 0, "dup": 0, "skipped": 0, "error": 0}
    target_cities = profile.get("target_cities") or []
    client = JobBankCSVClient(
        csv_path=csv_path, target_cities=target_cities,
    )
    return _persist_records(client, tracker, dry_run, limit)


def _run_manual_entry(
    profile_id: str, tracker, llm,
    dry_run: bool, limit: Optional[int],
    focus: Optional[str] = None,
) -> dict:
    """Manual entry is interactive (scripts/manual_entry.py).
    Not run in batch mode - the daily pipeline registers the
    handler so the source name validates, but it always returns
    zero counts."""
    return {
        "new": 0, "dup": 0, "skipped": 0, "error": 0,
        "note": "interactive only; use scripts/manual_entry.py",
    }


SOURCE_HANDLERS = {
    "greenhouse_api": _run_greenhouse,
    "jobspy": _run_jobspy,
    "linkedin_guest": _run_linkedin_guest,
    "lever_api": _run_lever,
    "ashby_api": _run_ashby,
    "workable_api": _run_workable,
    "personio_api": _run_personio,
    "recruitee_api": _run_recruitee,
    "workday": _run_workday,
    "email_monitor": _run_email_monitor,
    "job_bank_csv": _run_job_bank_csv,
    "manual_entry": _run_manual_entry,
}

# Sources that enumerate full employer boards (ATS) or read static
# data dumps. They aren't keyword-filtered, so an --focus ai run
# would just re-fetch everything they'd return on a no-flag run.
NON_KEYWORD_SOURCES = frozenset({
    "greenhouse_api", "lever_api", "ashby_api", "workable_api",
    "personio_api", "recruitee_api", "workday", "email_monitor",
    "job_bank_csv", "manual_entry",
})


def run_discovery(
    profile: dict, profile_id: str, tracker, llm,
    dry_run: bool, limit: Optional[int],
    focus: Optional[str] = None,
) -> dict[str, dict]:
    sources = list(profile.get("sources_enabled", []))
    if focus == "ai":
        skipped = [s for s in sources if s in NON_KEYWORD_SOURCES]
        sources = [s for s in sources if s not in NON_KEYWORD_SOURCES]
    else:
        skipped = []
    focus_label = focus or "all"
    print(
        f"\n--- DISCOVERY ({len(sources)} sources, focus={focus_label}) ---"
    )
    results: dict[str, dict] = {}
    for s in skipped:
        print(f"  {s}: skipped (--focus ai bypasses non-keyword sources)")
        results[s] = {"status": "skipped_focus_ai"}
    for source in sources:
        handler = SOURCE_HANDLERS.get(source)
        if handler is None:
            print(f"  {source}: not implemented, skipping")
            results[source] = {"status": "not_implemented"}
            continue
        try:
            print(f"  running {source}...")
            counts = handler(
                profile_id, tracker, llm, dry_run, limit, focus=focus,
            )
            results[source] = {"status": "ok", "counts": counts}
            print(
                f"    {source}: new={counts.get('new', 0)} "
                f"dup={counts.get('dup', 0)} "
                f"skipped={counts.get('skipped', 0)} "
                f"error={counts.get('error', 0)}"
            )
        except Exception as e:
            logger.exception("source %s failed", source)
            results[source] = {"status": "error", "error": str(e)}
            print(f"    {source}: ERROR -- {e}")
    return results


# --- Evaluation -----------------------------------------------------

DECIDE_EXCEPTION_RETRY_BUDGET = 2


def find_postings_needing_eval(
    tracker,
    target_version: Optional[str] = None,
    limit: Optional[int] = None,
    decide_exception_retry_budget: int = DECIDE_EXCEPTION_RETRY_BUDGET,
    current_versions: Optional[frozenset] = None,
) -> list[dict]:
    """Return postings that need (re-)evaluation under the v2.3 family.

    By default a posting is "current" if its latest eval is at any of
    CURRENT_EVALUATOR_VERSIONS (local pipeline, cloud, pre-filter
    skip, or local fallback). Pass current_versions to override, or
    a single target_version for legacy single-string semantics.

    Picks up:
      - postings with no eval at all
      - postings whose latest eval is at a version OUTSIDE the family
      - postings whose latest eval was coerced to EXPLORATORY by a
        Stage2CScoreError (reasoning contains 'decide_exception'),
        UP TO `decide_exception_retry_budget` retries — only the
        local pipeline (and its fallback variant, which runs the
        same code) emit that string, so the count is across both.
    """
    if current_versions is not None:
        versions = frozenset(current_versions)
    elif target_version is not None:
        versions = frozenset({target_version})
    else:
        versions = CURRENT_EVALUATOR_VERSIONS

    postings = tracker.list_opportunities(limit=limit or 10000)
    out: list[dict] = []
    for p in postings:
        latest = tracker.get_latest_evaluation(p["id"])
        if latest is None or latest.get("evaluator_version") not in versions:
            out.append(p)
            continue
        reasoning = (latest.get("reasoning") or "")
        if "decide_exception" not in reasoning:
            continue
        prior = tracker._query_one(
            "SELECT COUNT(*) AS n FROM eval_decisions "
            "WHERE opportunity_id = ? "
            "  AND evaluator_version IN (?, ?) "
            "  AND reasoning LIKE '%decide_exception%'",
            (p["id"], EVALUATOR_VERSION, FALLBACK_VERSION),
        )
        n_prior = int(prior["n"]) if prior else 0
        if n_prior < decide_exception_retry_budget:
            out.append(p)
    return out


def run_evaluation(
    profile_id: str, tracker, limit: Optional[int],
) -> dict:
    print("\n--- EVALUATION ---")
    if not check_ollama_available():
        print(
            f"ERROR: Ollama not reachable at {OLLAMA_HOST}. "
            "Is the service running?"
        )
        sys.exit(1)
    for required in (FILTER_MODEL, DECIDE_MODEL):
        if not check_model_present(required):
            print(
                f"ERROR: model '{required}' not present in Ollama. "
                f"Run: ollama pull {required}"
            )
            sys.exit(1)

    candidates = find_postings_needing_eval(tracker, limit=limit)
    print(f"  candidates needing v2.3 eval: {len(candidates)}")
    if not candidates:
        return {"evaluated": 0, "tier_distribution": {}}

    inv = InventoryTool(profile_id)
    profile_config = ProfileConfig(profile_id)
    inventory_summary = inv.get_summary()
    stage2a = Stage2a(inv, profile_config)

    t_start = time.time()
    verdicts = run_batch(
        candidates, inventory_summary, profile_config, stage2a,
    )
    elapsed = time.time() - t_start

    persisted = 0
    for pid, v in verdicts.items():
        try:
            persist_verdict(tracker, pid, v)
            persisted += 1
        except Exception:
            logger.exception("persist failed for posting %s", pid)
            print(f"  persist failed for posting {pid}")

    tier_dist = Counter(v.tier for v in verdicts.values())
    skip_at_2a = sum(1 for v in verdicts.values() if v.skip_at == "2a")
    skip_at_2pre = sum(1 for v in verdicts.values() if v.skip_at == "2pre")
    decided = sum(1 for v in verdicts.values() if v.skip_at is None)
    passed_2a = max(1, len(verdicts) - skip_at_2a)
    filter_rate = (skip_at_2pre / passed_2a) * 100

    print(f"  evaluated: {persisted} of {len(candidates)}")
    print(f"  tier distribution: {dict(tier_dist)}")
    print(
        f"  skip distribution: 2a={skip_at_2a} 2pre={skip_at_2pre} "
        f"decided={decided}"
    )
    print(f"  filter rate at 2pre: {filter_rate:.1f}% of 2a-passed")
    print(f"  wall: {elapsed:.0f}s ({elapsed/60:.1f} min)")
    return {
        "evaluated": persisted,
        "tier_distribution": dict(tier_dist),
        "filter_rate_at_2pre": filter_rate,
        "wall_seconds": elapsed,
    }


# --- Shortlist ------------------------------------------------------

SHORTLIST_TIERS = ("STRONG", "TOP_TIER")


def build_shortlist(tracker, target_version: str = EVALUATOR_VERSION) -> list[dict]:
    """Return list of {posting, eval_decision} dicts at STRONG/TOP_TIER
    from the latest target_version eval, sorted by fit_score DESC."""
    rows: list[dict] = []
    postings = tracker.list_opportunities(limit=10000)
    for p in postings:
        latest = tracker.get_latest_evaluation(p["id"])
        if not latest:
            continue
        if latest.get("evaluator_version") != target_version:
            continue
        if latest.get("tier") not in SHORTLIST_TIERS:
            continue
        rows.append({"posting": p, "decision": latest})
    rows.sort(key=lambda r: -(r["decision"].get("fit_score") or 0))
    return rows


def write_shortlist(
    profile_id: str, rows: list[dict],
    target_version: str = EVALUATOR_VERSION,
) -> Path:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"shortlist_{today}.md"
    lines: list[str] = []
    lines.append(f"# Daily Shortlist - {today}")
    lines.append("")
    lines.append(
        f"Profile: `{profile_id}`. Evaluator: `{target_version}`. "
        f"{len(rows)} postings at STRONG or TOP_TIER."
    )
    lines.append("")
    if not rows:
        lines.append("(none)")
    else:
        lines.append("| pid | tier | fit | employer | title | location |")
        lines.append("|---|---|---|---|---|---|")
        for r in rows:
            p = r["posting"]
            d = r["decision"]
            lines.append(
                f"| {p['id']} | {d.get('tier')} | "
                f"{d.get('fit_score')} | "
                f"{(p.get('employer') or '')[:40]} | "
                f"{(p.get('title') or '')[:60]} | "
                f"{(p.get('location') or '')[:30]} |"
            )
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def run_shortlist(
    profile_id: str, tracker,
    target_version: str = EVALUATOR_VERSION,
) -> tuple[Path, list[dict]]:
    print("\n--- SHORTLIST ---")
    rows = build_shortlist(tracker, target_version=target_version)
    out_path = write_shortlist(profile_id, rows, target_version=target_version)
    print(f"  wrote {out_path}")
    print(f"  postings on shortlist: {len(rows)}")
    if rows:
        print()
        print("  TOP 10:")
        for r in rows[:10]:
            p = r["posting"]
            d = r["decision"]
            print(
                f"  - [{d.get('tier')} fit={d.get('fit_score')}] "
                f"#{p['id']} {p.get('employer', '')} - "
                f"{p.get('title', '')}"
            )
    return out_path, rows


# --- Main -----------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--skip-discovery", action="store_true")
    parser.add_argument("--skip-eval", action="store_true")
    parser.add_argument(
        "--limit", type=int, default=None,
        help="cap per source / cap eval candidates (testing)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="discover but don't persist or evaluate",
    )
    parser.add_argument(
        "--focus", choices=["ai", "traditional"], default=None,
        help=(
            "Restrict discovery to AI keywords (--focus ai) or "
            "traditional keywords (--focus traditional). "
            "--focus ai also skips ATS/job-bank/manual sources, "
            "which aren't keyword-filtered. Default: both."
        ),
    )
    parser.add_argument(
        "--evaluator", choices=["local", "cloud"], default="local",
        help=(
            "Evaluator backend. 'local' uses the existing 3-stage "
            "Gemma pipeline (default). 'cloud' uses Gemma 4 31B via "
            "Google AI Studio API (free tier, 1500 RPD)."
        ),
    )
    parser.add_argument(
        "--source", default=None,
        help=(
            "Restrict discovery to a single source name from the "
            "profile's sources_enabled (e.g. --source greenhouse_api). "
            "Errors out if the name isn't in sources_enabled."
        ),
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    log_dir = PROJECT_ROOT / "scripts" / "output"
    log_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = log_dir / f"daily_log_{today}.log"
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    logging.getLogger().addHandler(file_handler)
    # Stable-name backup log (append) — survives a locked DB and
    # gives the dashboard a single file to tail across runs.
    backup_handler = logging.FileHandler(
        log_dir / "daily_run.log", mode="a", encoding="utf-8",
    )
    backup_handler.setLevel(logging.INFO)
    backup_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    logging.getLogger().addHandler(backup_handler)
    sys.stdout = TeeWriter(log_path)

    import time as _time
    import traceback as _traceback
    _run_start = _time.monotonic()
    tracker = None
    try:
        profile = load_profile(args.profile)
        if not profile:
            print(f"ERROR: profile '{args.profile}' not found at "
                  f"{_profile_yaml_path(args.profile)}")
            return 1

        if args.source:
            enabled = profile.get("sources_enabled", []) or []
            if args.source not in enabled:
                print(
                    f"ERROR: --source '{args.source}' not in profile's "
                    f"sources_enabled. Valid options: {enabled}",
                    file=sys.stderr,
                )
                return 2
            profile["sources_enabled"] = [args.source]

        tracker = Tracker(args.profile)
        llm = LLMClient()
        log_activity(
            args.profile, "scheduled_task", "task_fired", "running",
            "Nightly run started",
        )

        discovery_results: dict[str, dict] = {}
        eval_results: dict = {}
        shortlist_path: Optional[Path] = None
        shortlist_count = 0
        if not args.skip_discovery:
            discovery_results = run_discovery(
                profile, args.profile, tracker, llm,
                dry_run=args.dry_run, limit=args.limit,
                focus=args.focus,
            )
            _new = sum(
                (r.get("counts") or {}).get("new", 0)
                for r in discovery_results.values()
                if r.get("status") == "ok"
            )
            log_activity(
                args.profile, "discovery", "run_completed", "success",
                f"Discovery: {_new} new across "
                f"{len(discovery_results)} sources",
                details={"results": discovery_results},
            )
        else:
            print("\n--- DISCOVERY skipped (--skip-discovery) ---")

        if args.dry_run:
            print("\n--- DRY RUN: stopping before eval/shortlist ---")
            return 0

        shortlist_version = EVALUATOR_VERSION
        if not args.skip_eval:
            if args.evaluator == "cloud":
                from skills.role_evaluator.cloud_pipeline import (
                    run_batch_cloud,
                    EVALUATOR_VERSION as CLOUD_VERSION,
                )
                eval_results = run_batch_cloud(
                    tracker, args.profile,
                    auto=True, limit=args.limit,
                )
                shortlist_version = CLOUD_VERSION
            else:
                eval_results = run_evaluation(
                    args.profile, tracker, limit=args.limit,
                )
            log_activity(
                args.profile, "evaluation", "run_completed", "success",
                f"Evaluated {eval_results.get('evaluated', 0)} postings",
                details={
                    "tier_distribution":
                        eval_results.get("tier_distribution", {}),
                },
            )
        else:
            print("\n--- EVAL skipped (--skip-eval) ---")

        path, rows = run_shortlist(
            args.profile, tracker, target_version=shortlist_version,
        )
        shortlist_path = path
        shortlist_count = len(rows)

        bar = "=" * 70
        print()
        print(bar)
        print("DAILY PIPELINE COMPLETE")
        print(bar)
        print(f"Profile:   {args.profile}")
        print(f"Evaluator: {EVALUATOR_VERSION}")
        if discovery_results:
            print("Discovery:")
            for src, r in discovery_results.items():
                if r.get("status") == "ok":
                    c = r.get("counts", {})
                    print(
                        f"  {src}: new={c.get('new', 0)} "
                        f"dup={c.get('dup', 0)} "
                        f"err={c.get('error', 0)}"
                    )
                else:
                    print(f"  {src}: {r.get('status')}")
        if eval_results:
            print(
                f"Evaluated: {eval_results.get('evaluated', 0)} "
                f"-> {eval_results.get('tier_distribution', {})}"
            )
        print(f"Shortlist: {shortlist_count} postings -> {shortlist_path}")
        print(bar)
        _elapsed_ms = int((_time.monotonic() - _run_start) * 1000)
        log_activity(
            args.profile, "scheduled_task", "task_completed", "success",
            f"Nightly run completed — {shortlist_count} shortlisted",
            duration_ms=_elapsed_ms,
        )
        return 0
    except Exception as e:
        _elapsed_ms = int((_time.monotonic() - _run_start) * 1000)
        log_activity(
            args.profile, "scheduled_task", "task_failed", "error",
            f"Nightly run failed: {type(e).__name__}: {e}",
            duration_ms=_elapsed_ms,
            error_message=_traceback.format_exc()[-2000:],
        )
        raise
    finally:
        if tracker is not None:
            tracker.close()
        if isinstance(sys.stdout, TeeWriter):
            sys.stdout.close()
            sys.stdout = sys.stdout._stdout
        logging.getLogger().removeHandler(file_handler)
        file_handler.close()
        logging.getLogger().removeHandler(backup_handler)
        backup_handler.close()


if __name__ == "__main__":
    sys.exit(main())
