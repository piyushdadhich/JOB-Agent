"""Tests for engine.resume.auto_prompt -- batch prompt generation
called from the cloud pipeline after evaluation.

Uses a fake PromptService so we don't need a real career_inventory.md
or live builders. Verifies the persistence + idempotency behavior.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.persistence.tracker import Tracker  # noqa: E402
from engine.resume.auto_prompt import auto_generate_prompts  # noqa: E402


class FakePromptService:
    """Stub PromptService whose builders just echo posting + a marker."""

    def __init__(self, inventory_marker: str = "INV-MARKER"):
        self.inventory_marker = inventory_marker
        self.resume_calls: list[dict] = []
        self.cl_calls: list[dict] = []

    def build_resume_prompt(self, *, profile_id, posting, eval_decision=None):
        self.resume_calls.append(
            {"profile_id": profile_id, "posting_id": posting.get("id")}
        )
        return (
            f"RESUME PROMPT for {posting['title']} @ {posting['employer']} "
            f"\n{self.inventory_marker}"
        )

    def build_cover_letter_prompt(self, *, profile_id, posting, eval_decision=None):
        self.cl_calls.append(
            {"profile_id": profile_id, "posting_id": posting.get("id")}
        )
        return (
            f"COVER LETTER PROMPT for {posting['title']} @ {posting['employer']} "
            f"\n{self.inventory_marker}"
        )


@pytest.fixture
def tracker(tmp_path):
    db = tmp_path / "t.db"
    t = Tracker(profile_id="p", db_path=db)
    yield t
    t.close()


def _add_posting(t, employer, title):
    cid = t.upsert_company(employer)
    opp_id, _ = t.insert_opportunity(
        company_id=cid, source="manual_entry",
        source_url=f"https://x/{employer}/{title}",
        title=title, location="Toronto",
        posting_text=f"Lead {title} at {employer}.",
    )
    return opp_id


def test_auto_generate_creates_application_row(tracker):
    opp_id = _add_posting(tracker, "TD", "Senior PM")
    svc = FakePromptService()

    outcome = auto_generate_prompts(
        tracker, [opp_id], profile_id="default", prompt_service=svc,
    )

    assert outcome == {"generated": 1, "skipped_existing": 0, "skipped_missing": 0}
    row = tracker._query_one(
        "SELECT id, resume_prompt, cover_letter_prompt, selected_at "
        "FROM applications WHERE opportunity_id = ?", (opp_id,),
    )
    assert row is not None
    assert "RESUME PROMPT" in row["resume_prompt"]
    assert "COVER LETTER PROMPT" in row["cover_letter_prompt"]
    assert row["selected_at"] is not None


def test_auto_generate_skips_when_both_prompts_present(tracker):
    opp_id = _add_posting(tracker, "BMO", "Delivery Lead")
    svc = FakePromptService()

    auto_generate_prompts(
        tracker, [opp_id], profile_id="default", prompt_service=svc,
    )
    assert len(svc.resume_calls) == 1

    outcome = auto_generate_prompts(
        tracker, [opp_id], profile_id="default", prompt_service=svc,
    )

    assert outcome == {"generated": 0, "skipped_existing": 1, "skipped_missing": 0}
    # No additional builder calls -- truly idempotent.
    assert len(svc.resume_calls) == 1
    assert len(svc.cl_calls) == 1


def test_auto_generate_skips_unknown_opportunity(tracker):
    svc = FakePromptService()
    outcome = auto_generate_prompts(
        tracker, [99999], profile_id="default", prompt_service=svc,
    )
    assert outcome == {"generated": 0, "skipped_existing": 0, "skipped_missing": 1}
    assert svc.resume_calls == []


def test_auto_generate_prompts_contain_posting_details(tracker):
    opp_id = _add_posting(tracker, "TestCo", "Payments Officer")
    svc = FakePromptService()

    auto_generate_prompts(
        tracker, [opp_id], profile_id="default", prompt_service=svc,
    )

    row = tracker._query_one(
        "SELECT resume_prompt, cover_letter_prompt FROM applications "
        "WHERE opportunity_id = ?", (opp_id,),
    )
    assert "Payments Officer" in row["resume_prompt"]
    assert "TestCo" in row["resume_prompt"]
    assert "Payments Officer" in row["cover_letter_prompt"]
    assert "TestCo" in row["cover_letter_prompt"]


def test_auto_generate_prompts_contain_inventory_marker(tracker):
    opp_id = _add_posting(tracker, "CIBC", "Scrum Master")
    svc = FakePromptService(inventory_marker="CAREER-INVENTORY-CONTENT")

    auto_generate_prompts(
        tracker, [opp_id], profile_id="default", prompt_service=svc,
    )

    row = tracker._query_one(
        "SELECT resume_prompt, cover_letter_prompt FROM applications "
        "WHERE opportunity_id = ?", (opp_id,),
    )
    assert "CAREER-INVENTORY-CONTENT" in row["resume_prompt"]
    assert "CAREER-INVENTORY-CONTENT" in row["cover_letter_prompt"]


def test_auto_generate_returns_outcome_counts(tracker):
    opp_a = _add_posting(tracker, "TD", "PM A")
    opp_b = _add_posting(tracker, "BMO", "PM B")
    opp_c = _add_posting(tracker, "CIBC", "PM C")
    svc = FakePromptService()

    # Pre-generate for one of them so it gets skipped on the next run.
    auto_generate_prompts(
        tracker, [opp_b], profile_id="default", prompt_service=svc,
    )

    outcome = auto_generate_prompts(
        tracker,
        [opp_a, opp_b, opp_c, 99999],
        profile_id="default",
        prompt_service=svc,
    )

    assert outcome["generated"] == 2  # opp_a + opp_c
    assert outcome["skipped_existing"] == 1  # opp_b
    assert outcome["skipped_missing"] == 1  # 99999


# --- Integration: cloud pipeline calls auto_generate after eval ---


def test_cloud_pipeline_calls_auto_generate_after_eval(tracker, monkeypatch):
    """run_batch_cloud should call auto_generate_prompts for every
    posting whose verdict is TOP_TIER or STRONG, and skip the rest."""
    from skills.role_evaluator import cloud_pipeline as cp
    from skills.role_evaluator.stage2a import Stage2aResult

    opp_top = _add_posting(tracker, "TD", "Senior PM Top")
    opp_strong = _add_posting(tracker, "BMO", "Delivery Lead Strong")
    opp_explore = _add_posting(tracker, "CIBC", "Scrum Master Exp")

    parsed_by_id = {
        opp_top: {
            "verdict": "PROCEED", "skip_reason": None,
            "scores": {"function": 3, "domain": 3, "seniority": 3,
                       "disqualifier": False},
        },
        opp_strong: {
            "verdict": "PROCEED", "skip_reason": None,
            "scores": {"function": 3, "domain": 1, "seniority": 2,
                       "disqualifier": False},
        },
        opp_explore: {
            "verdict": "PROCEED", "skip_reason": None,
            "scores": {"function": 1, "domain": 1, "seniority": 1,
                       "disqualifier": False},
        },
    }

    # Stub heavy dependencies so run_batch_cloud doesn't touch disk/network.
    monkeypatch.setattr(cp, "_load_prompt_template", lambda: "prompt")
    monkeypatch.setattr(
        cp, "_load_compact_inventory", lambda pid, y: "inventory",
    )
    monkeypatch.setattr(cp, "_load_profile_yaml", lambda pid: {})

    class _FakeStage2a:
        def __init__(self, *a, **k):
            pass

        def evaluate(self, posting):
            return type("R", (), {"result": Stage2aResult.PASS_TO_2B,
                                  "rule_fired": None})()

    monkeypatch.setattr(cp, "Stage2a", _FakeStage2a)
    monkeypatch.setattr(cp, "InventoryTool", lambda pid: object())
    monkeypatch.setattr(cp, "ProfileConfig", lambda pid: object())
    monkeypatch.setattr(cp, "GemmaCloudClient", lambda profile_id: object())

    fake_postings = [
        tracker.get_opportunity_by_id(opp_top),
        tracker.get_opportunity_by_id(opp_strong),
        tracker.get_opportunity_by_id(opp_explore),
    ]

    monkeypatch.setattr(
        "scripts.run_daily.find_postings_needing_eval",
        lambda tracker, *args, **kwargs: fake_postings,
    )

    # CallManager.execute -> drives persist_fn for each posting.
    class _FakeCallManager:
        def __init__(self, *, persist_fn, **kwargs):
            self.persist_fn = persist_fn
            self.find_fn = kwargs["find_postings_fn"]

        def inventory(self):
            return {}

        def plan(self, _inv):
            return {"deferred_new": 0, "deferred_retries": 0}

        def execute(self, _plan, auto=True):
            for p in self.find_fn():
                self.persist_fn(p, parsed_by_id[p["id"]])
            return {"evaluated": 3, "proceeded": 3,
                    "skipped_by_model": 0, "failed": 0}

        def report(self, _r, _p):
            return ""

    monkeypatch.setattr(cp, "CallManager", _FakeCallManager)

    # Inject the FakePromptService into auto_prompt so the test
    # doesn't need a real career_inventory.md.
    from engine.resume import auto_prompt as ap
    real_auto = ap.auto_generate_prompts
    captured: dict = {}

    def _wrapper(tracker, opportunity_ids, profile_id, prompt_service=None):
        captured["ids"] = list(opportunity_ids)
        return real_auto(
            tracker, opportunity_ids, profile_id,
            prompt_service=FakePromptService(),
        )

    monkeypatch.setattr(ap, "auto_generate_prompts", _wrapper)

    # Re-import-time hook: cloud_pipeline imports auto_generate_prompts
    # lazily inside run_batch_cloud, so monkey-patching ap is sufficient.

    result = cp.run_batch_cloud(tracker, profile_id="default", auto=True)

    # Only TOP_TIER + STRONG should have been auto-prompted.
    assert sorted(captured["ids"]) == sorted([opp_top, opp_strong])
    assert result["auto_prompts_generated"] == 2

    # Verify persistence: the two top postings have prompts stamped.
    for oid in (opp_top, opp_strong):
        row = tracker._query_one(
            "SELECT resume_prompt, cover_letter_prompt, selected_at "
            "FROM applications WHERE opportunity_id = ?", (oid,),
        )
        assert row is not None
        assert row["resume_prompt"] is not None
        assert row["cover_letter_prompt"] is not None
        assert row["selected_at"] is not None

    # The exploratory posting did NOT get auto-prompts.
    explore_row = tracker._query_one(
        "SELECT id FROM applications WHERE opportunity_id = ?", (opp_explore,),
    )
    assert explore_row is None
