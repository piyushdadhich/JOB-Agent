"""Unit tests for scripts/run_daily.py.

Mocks all external dependencies (HTTP, Tracker, LLM clients,
discovery clients). No live Ollama, no scrapers, no DB.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_daily import (  # noqa: E402
    EVALUATOR_VERSION,
    SHORTLIST_TIERS,
    SOURCE_HANDLERS,
    build_shortlist,
    check_model_present,
    check_ollama_available,
    find_postings_needing_eval,
    load_profile,
    main,
    run_discovery,
)


# --- load_profile --------------------------------------------------

def test_load_profile_returns_dict(tmp_path, monkeypatch):
    """load_profile reads the YAML and returns a dict."""
    fake_root = tmp_path
    (fake_root / "config" / "profiles").mkdir(parents=True)
    (fake_root / "config" / "profiles" / "test.yaml").write_text(
        "profile_id: test\nsources_enabled: [a, b]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "scripts.run_daily.PROJECT_ROOT", fake_root,
    )
    cfg = load_profile("test")
    assert cfg["profile_id"] == "test"
    assert cfg["sources_enabled"] == ["a", "b"]


def test_load_profile_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "scripts.run_daily.PROJECT_ROOT", tmp_path,
    )
    assert load_profile("nope") == {}


# --- Ollama probes -------------------------------------------------

def test_check_ollama_available_true_on_200():
    fake = MagicMock(status_code=200)
    with patch("scripts.run_daily.requests.get", return_value=fake):
        assert check_ollama_available("http://h:1") is True


def test_check_ollama_available_false_on_connection_error():
    with patch(
        "scripts.run_daily.requests.get",
        side_effect=ConnectionError("down"),
    ):
        assert check_ollama_available("http://h:1") is False


def test_check_model_present_true_when_in_tags():
    fake = MagicMock()
    fake.raise_for_status = MagicMock()
    fake.json.return_value = {
        "models": [{"name": "gemma4:e4b"}, {"name": "gemma3-4b-ctx4k"}]
    }
    with patch("scripts.run_daily.requests.get", return_value=fake):
        assert check_model_present("gemma4:e4b") is True
        assert check_model_present("gemma3-4b-ctx4k") is True


def test_check_model_present_false_when_missing():
    fake = MagicMock()
    fake.raise_for_status = MagicMock()
    fake.json.return_value = {"models": [{"name": "other:tag"}]}
    with patch("scripts.run_daily.requests.get", return_value=fake):
        assert check_model_present("gemma4:e4b") is False


# --- run_discovery -------------------------------------------------

def test_source_handlers_include_expected_sources():
    """Architectural guardrail: greenhouse_api, jobspy, and
    linkedin_guest must have live handlers wired."""
    assert "greenhouse_api" in SOURCE_HANDLERS
    assert "jobspy" in SOURCE_HANDLERS
    assert "linkedin_guest" in SOURCE_HANDLERS


def test_run_discovery_logs_not_implemented_for_unknown_source(capsys):
    profile = {"sources_enabled": ["never_implemented_source"]}
    results = run_discovery(
        profile, "test", tracker=MagicMock(), llm=MagicMock(),
        dry_run=False, limit=None,
    )
    assert results["never_implemented_source"]["status"] == "not_implemented"
    captured = capsys.readouterr()
    assert "not implemented" in captured.out


def test_run_discovery_continues_on_handler_error(capsys):
    profile = {"sources_enabled": ["greenhouse_api", "jobspy"]}
    handlers = {
        "greenhouse_api": MagicMock(side_effect=RuntimeError("boom")),
        "jobspy": MagicMock(return_value={"new": 5, "dup": 0, "skipped": 0, "error": 0}),
    }
    with patch.dict(SOURCE_HANDLERS, handlers, clear=True):
        results = run_discovery(
            profile, "test", tracker=MagicMock(), llm=MagicMock(),
            dry_run=False, limit=None,
        )
    assert results["greenhouse_api"]["status"] == "error"
    assert "boom" in results["greenhouse_api"]["error"]
    assert results["jobspy"]["status"] == "ok"
    assert results["jobspy"]["counts"]["new"] == 5


def test_run_discovery_passes_limit_and_dry_run_through():
    handler = MagicMock(return_value={"new": 0, "dup": 0, "skipped": 0, "error": 0})
    profile = {"sources_enabled": ["greenhouse_api"]}
    with patch.dict(SOURCE_HANDLERS, {"greenhouse_api": handler}, clear=True):
        run_discovery(
            profile, "test_profile",
            tracker="TRACKER", llm="LLM",
            dry_run=True, limit=42,
        )
    handler.assert_called_once_with(
        "test_profile", "TRACKER", "LLM", True, 42, focus=None,
    )


# --- find_postings_needing_eval -----------------------------------

def test_find_postings_needing_eval_includes_unevaluated():
    tracker = MagicMock()
    tracker.list_opportunities.return_value = [
        {"id": 1}, {"id": 2}, {"id": 3},
    ]

    def latest(pid):
        return None if pid == 1 else {"evaluator_version": EVALUATOR_VERSION}

    tracker.get_latest_evaluation.side_effect = latest
    result = find_postings_needing_eval(tracker)
    assert [p["id"] for p in result] == [1]


def test_find_postings_needing_eval_includes_stale_version():
    tracker = MagicMock()
    tracker.list_opportunities.return_value = [{"id": 10}, {"id": 20}]
    tracker.get_latest_evaluation.side_effect = (
        lambda pid: {"evaluator_version": (
            "pipeline-v2.3.0" if pid == 10 else "old-v1"
        )}
    )
    result = find_postings_needing_eval(tracker)
    assert [p["id"] for p in result] == [20]


def test_find_postings_needing_eval_self_heals_decide_exception():
    """Postings whose prior eval was coerced to EXPLORATORY by a
    stage_2c_score crash carry 'decide_exception' in their reasoning
    field. Pick them up for re-eval rather than leaving them stuck
    at EXPLORATORY/None forever."""
    tracker = MagicMock()
    tracker.list_opportunities.return_value = [
        {"id": 1}, {"id": 2}, {"id": 3},
    ]

    def latest(pid):
        return {
            1: {
                "evaluator_version": EVALUATOR_VERSION,
                "tier": "STRONG",
                "reasoning": "fn=2; dom=1; sen=1; norm=70.0",
            },
            2: {
                "evaluator_version": EVALUATOR_VERSION,
                "tier": "EXPLORATORY",
                "reasoning": (
                    "[skip_reason] decide_exception: "
                    "Stage2CScoreError"
                ),
            },
            3: {
                "evaluator_version": EVALUATOR_VERSION,
                "tier": "EXPLORATORY",
                "reasoning": "fn=1; dom=2; sen=2; norm=45.0",
            },
        }[pid]

    tracker.get_latest_evaluation.side_effect = latest
    # First retry: posting #2 has 1 prior decide_exception row,
    # under the default budget of 2 → enqueue.
    tracker._query_one.return_value = {"n": 1}
    result = find_postings_needing_eval(tracker)
    assert [p["id"] for p in result] == [2]


def test_find_postings_needing_eval_retry_budget_capped():
    """Once a posting has hit decide_exception >= retry_budget
    times, stop re-queuing it — a reproducible parser bug
    shouldn't pile up eval_decisions rows on every daily run."""
    tracker = MagicMock()
    tracker.list_opportunities.return_value = [{"id": 1}]
    tracker.get_latest_evaluation.return_value = {
        "evaluator_version": EVALUATOR_VERSION,
        "tier": "EXPLORATORY",
        "reasoning": "[skip_reason] decide_exception: Stage2CScoreError",
    }
    # 5 prior exception rows already — way past the retry budget.
    tracker._query_one.return_value = {"n": 5}
    result = find_postings_needing_eval(
        tracker, decide_exception_retry_budget=2,
    )
    assert result == []


# --- build_shortlist -----------------------------------------------

def test_build_shortlist_filters_to_strong_and_top_tier():
    tracker = MagicMock()
    tracker.list_opportunities.return_value = [
        {"id": 1, "title": "A"},
        {"id": 2, "title": "B"},
        {"id": 3, "title": "C"},
        {"id": 4, "title": "D"},
    ]

    def latest(pid):
        # pid 1: TOP_TIER, pid 2: STRONG, pid 3: EXPLORATORY, pid 4: SKIP
        return {
            1: {"evaluator_version": EVALUATOR_VERSION,
                "tier": "TOP_TIER", "fit_score": 9},
            2: {"evaluator_version": EVALUATOR_VERSION,
                "tier": "STRONG", "fit_score": 7},
            3: {"evaluator_version": EVALUATOR_VERSION,
                "tier": "EXPLORATORY", "fit_score": 5},
            4: {"evaluator_version": EVALUATOR_VERSION,
                "tier": "SKIP", "fit_score": 1},
        }[pid]

    tracker.get_latest_evaluation.side_effect = latest
    rows = build_shortlist(tracker)
    pids = [r["posting"]["id"] for r in rows]
    assert pids == [1, 2]


def test_build_shortlist_skips_wrong_evaluator_version():
    """A STRONG verdict from a stale evaluator must NOT appear on
    the shortlist - we want only the current pipeline-v2.3.0 ones."""
    tracker = MagicMock()
    tracker.list_opportunities.return_value = [{"id": 99}]
    tracker.get_latest_evaluation.return_value = {
        "evaluator_version": "old-v1", "tier": "STRONG", "fit_score": 8,
    }
    rows = build_shortlist(tracker)
    assert rows == []


def test_build_shortlist_sorted_by_fit_score_desc():
    tracker = MagicMock()
    tracker.list_opportunities.return_value = [
        {"id": 1}, {"id": 2}, {"id": 3},
    ]

    def latest(pid):
        scores = {1: 5, 2: 9, 3: 7}
        return {
            "evaluator_version": EVALUATOR_VERSION,
            "tier": "STRONG",
            "fit_score": scores[pid],
        }

    tracker.get_latest_evaluation.side_effect = latest
    rows = build_shortlist(tracker)
    pids = [r["posting"]["id"] for r in rows]
    assert pids == [2, 3, 1]


def test_build_shortlist_skips_postings_without_eval():
    tracker = MagicMock()
    tracker.list_opportunities.return_value = [{"id": 1}, {"id": 2}]
    tracker.get_latest_evaluation.side_effect = (
        lambda pid: None if pid == 1 else {
            "evaluator_version": EVALUATOR_VERSION,
            "tier": "STRONG", "fit_score": 6,
        }
    )
    rows = build_shortlist(tracker)
    assert [r["posting"]["id"] for r in rows] == [2]


# --- Constants -----------------------------------------------------

def test_shortlist_tiers_are_strong_and_top_tier():
    assert SHORTLIST_TIERS == ("STRONG", "TOP_TIER")


# --- File logging --------------------------------------------------

def _patch_main_for_logging_test(tmp_path, monkeypatch):
    """Common harness: redirect PROJECT_ROOT, mock heavy components."""
    monkeypatch.setattr("scripts.run_daily.PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("scripts.run_daily.Tracker", MagicMock())
    monkeypatch.setattr("scripts.run_daily.LLMClient", MagicMock())
    monkeypatch.setattr(
        "scripts.run_daily.load_profile",
        lambda pid: {"sources_enabled": []},
    )
    monkeypatch.setattr(
        "scripts.run_daily.run_shortlist",
        MagicMock(return_value=(tmp_path / "shortlist.md", [])),
    )


def test_log_file_created_on_run(tmp_path, monkeypatch):
    """A daily_log_{YYYY-MM-DD}.log file is written to scripts/output."""
    _patch_main_for_logging_test(tmp_path, monkeypatch)

    rc = main(["--profile", "test", "--skip-discovery", "--skip-eval"])
    assert rc == 0

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = tmp_path / "scripts" / "output" / f"daily_log_{today}.log"
    assert log_file.exists()


def test_log_file_contains_pipeline_output(tmp_path, monkeypatch):
    """TeeWriter duplicates print() output (the summary banner, skipped
    phase notices) into the daily log file."""
    _patch_main_for_logging_test(tmp_path, monkeypatch)

    rc = main(["--profile", "test", "--skip-discovery", "--skip-eval"])
    assert rc == 0

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = tmp_path / "scripts" / "output" / f"daily_log_{today}.log"
    content = log_file.read_text(encoding="utf-8")
    assert "DAILY PIPELINE COMPLETE" in content
    assert "DISCOVERY skipped" in content
    assert "EVAL skipped" in content


# --- --focus flag --------------------------------------------------

def _make_profile_with_role_types(role_type_ids):
    """Build a fake hydrated Profile with the given role_type ids."""
    from engine.profiles.loader import Profile, RoleType
    role_types = [
        RoleType(id=rid, description=f"{rid} desc", search_terms=[rid])
        for rid in role_type_ids
    ]
    return Profile(
        profile_id="test", display_name="Test", domain="corporate",
        target_cities=["toronto"], target_role_types=role_types,
        sources_enabled=["jobspy"], source_config={},
    )


def _run_jobspy_capturing_profile(profile, focus):
    """Invoke _run_jobspy with all external deps stubbed; return the
    Profile that JobSpyClient was constructed with."""
    from scripts.run_daily import _run_jobspy
    captured: dict = {}

    def fake_client_ctor(profile=None, **_kw):
        captured["profile"] = profile
        return MagicMock()

    with patch(
        "engine.profiles.loader.load_profile_from_default",
        return_value=profile,
    ), patch(
        "engine.discovery.jobspy_client.JobSpyClient",
        side_effect=fake_client_ctor,
    ), patch(
        "scripts.run_daily._persist_records",
        return_value={"new": 0, "dup": 0, "skipped": 0, "error": 0},
    ):
        _run_jobspy(
            "test", tracker=MagicMock(), llm=MagicMock(),
            dry_run=False, limit=None, focus=focus,
        )
    return captured.get("profile")


def test_focus_ai_filters_to_ai_role_types_only():
    """--focus ai keeps only ai_roles in Profile.target_role_types."""
    profile = _make_profile_with_role_types(
        ["delivery_manager", "scrum_master", "ai_roles"]
    )
    constructed = _run_jobspy_capturing_profile(profile, focus="ai")
    rt_ids = [rt.id for rt in constructed.target_role_types]
    assert rt_ids == ["ai_roles"]


def test_focus_traditional_excludes_ai_role_types():
    """--focus traditional drops ai_roles, preserves other role types."""
    profile = _make_profile_with_role_types(
        ["delivery_manager", "scrum_master", "ai_roles"]
    )
    constructed = _run_jobspy_capturing_profile(
        profile, focus="traditional",
    )
    rt_ids = [rt.id for rt in constructed.target_role_types]
    assert rt_ids == ["delivery_manager", "scrum_master"]


def test_focus_none_passes_all_role_types():
    """No --focus flag preserves the full target_role_types list
    (backward-compatible default)."""
    role_ids = ["delivery_manager", "scrum_master", "ai_roles"]
    profile = _make_profile_with_role_types(role_ids)
    constructed = _run_jobspy_capturing_profile(profile, focus=None)
    rt_ids = [rt.id for rt in constructed.target_role_types]
    assert rt_ids == role_ids


def test_focus_ai_skips_ats_sources():
    """run_discovery with --focus ai must not invoke ATS, job-bank,
    or manual handlers — they aren't keyword-filtered."""
    profile = {
        "sources_enabled": [
            "jobspy", "linkedin_guest",
            "greenhouse_api", "lever_api", "ashby_api",
            "workable_api", "personio_api", "recruitee_api",
            "job_bank_csv", "manual_entry",
        ],
    }
    zero = {"new": 0, "dup": 0, "skipped": 0, "error": 0}
    handlers = {
        s: MagicMock(return_value=zero)
        for s in profile["sources_enabled"]
    }
    with patch.dict(SOURCE_HANDLERS, handlers, clear=True):
        results = run_discovery(
            profile, "test", tracker=MagicMock(), llm=MagicMock(),
            dry_run=False, limit=None, focus="ai",
        )

    # Keyword-searchable sources DO run with focus passed through
    handlers["jobspy"].assert_called_once_with(
        "test", ANY, ANY, False, None, focus="ai",
    )
    handlers["linkedin_guest"].assert_called_once_with(
        "test", ANY, ANY, False, None, focus="ai",
    )
    # Everything else is skipped
    skipped_sources = [
        "greenhouse_api", "lever_api", "ashby_api",
        "workable_api", "personio_api", "recruitee_api",
        "job_bank_csv", "manual_entry",
    ]
    for src in skipped_sources:
        handlers[src].assert_not_called()
        assert results[src]["status"] == "skipped_focus_ai"
