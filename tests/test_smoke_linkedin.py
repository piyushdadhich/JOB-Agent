"""Tests for scripts/smoke_linkedin.py (Spec A2 TASK 5).

The smoke harness exercises real persistence against a temp DB by
monkey-patching the modules' Tracker constructor and the
load_profile_from_default callable, then yielding synthetic
OpportunityRecord objects from a stub client. All HTTP is avoided.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.discovery.base import OpportunityRecord  # noqa: E402
from engine.persistence.tracker import Tracker  # noqa: E402


def _load_smoke_module():
    spec = importlib.util.spec_from_file_location(
        "_smoke_linkedin_test_mod",
        PROJECT_ROOT / "scripts" / "smoke_linkedin.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # Register so lazy imports inside the module (and helpers below)
    # can resolve the symbol.
    sys.modules["_smoke_linkedin_test_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


def _make_record(
    *,
    title="PM",
    employer="Acme",
    url="https://www.linkedin.com/jobs/view/100",
    hiring_team=None,
) -> OpportunityRecord:
    return OpportunityRecord(
        source="linkedin_guest",
        source_url=url,
        employer=employer,
        title=title,
        location="Toronto",
        posting_text="Body.",
        date_discovered=datetime.now(timezone.utc),
        raw_payload={"hiring_team": hiring_team or []},
        search_context={"job_id": url.rsplit("/", 1)[-1]},
    )


class _StubClient:
    def __init__(
        self,
        records,
        *,
        raise_breakage: bool = False,
    ):
        self._records = records
        self._raise = raise_breakage
        self.config = SimpleNamespace(
            keywords=["pm"],
            locations=["Toronto"],
            time_filter="r604800",
            max_pages_per_query=5,
            fetch_details=True,
            list_rate_limit_seconds=0,
            detail_rate_limit_seconds=0,
            card_text_skip_threshold=200,
        )
        self._rate_limit_escalated = False
        self._current_list_rate_limit = 0
        self._current_detail_rate_limit = 0

    def fetch(self):
        for r in self._records:
            yield r
        if self._raise:
            # Imported lazily so the test setup doesn't depend on it.
            from _smoke_linkedin_test_mod import SelectorBreakageError
            raise SelectorBreakageError("simulated >50% miss rate")


def _wire_smoke(mod, monkeypatch, tmp_path, client):
    """Common setup: every Tracker construction inside the smoke
    module points at the tmp db, and load_profile_from_default
    returns a fake profile object."""
    fake_profile = SimpleNamespace(profile_id="p")
    monkeypatch.setattr(
        mod, "load_profile_from_default", lambda _id: fake_profile,
    )

    def fake_tracker(*, profile_id):
        return Tracker(profile_id=profile_id, db_path=tmp_path / "t.db")

    monkeypatch.setattr(mod, "Tracker", fake_tracker)
    monkeypatch.setattr(
        mod, "LinkedInGuestClient", lambda profile: client,
    )


def test_smoke_persists_yielded_records(tmp_path, monkeypatch):
    mod = _load_smoke_module()
    rec1 = _make_record(url="https://www.linkedin.com/jobs/view/1")
    rec2 = _make_record(
        url="https://www.linkedin.com/jobs/view/2",
        hiring_team=[{
            "name": "Alice", "title": "Director",
            "profile_url": "https://www.linkedin.com/in/alice",
            "role": "hiring_team",
        }],
    )
    client = _StubClient([rec1, rec2])
    _wire_smoke(mod, monkeypatch, tmp_path, client)

    rc = mod.main(["--profile", "p"])
    assert rc == 0

    t = Tracker(profile_id="p", db_path=tmp_path / "t.db")
    try:
        opps = t._query_all("SELECT * FROM opportunities")
        assert len(opps) == 2
        posters = t._query_all("SELECT * FROM job_posters")
        assert len(posters) == 1
        links = t._query_all("SELECT * FROM opportunity_posters")
        assert len(links) == 1
    finally:
        t.close()


def test_smoke_handles_incomplete_record_error(tmp_path, monkeypatch):
    mod = _load_smoke_module()
    # employer="" makes persist_record raise IncompleteRecordError.
    rec_bad = _make_record(employer="")
    rec_good = _make_record(url="https://www.linkedin.com/jobs/view/3")
    client = _StubClient([rec_bad, rec_good])
    _wire_smoke(mod, monkeypatch, tmp_path, client)

    rc = mod.main(["--profile", "p"])
    assert rc == 0

    t = Tracker(profile_id="p", db_path=tmp_path / "t.db")
    try:
        opps = t._query_all("SELECT * FROM opportunities")
        assert len(opps) == 1
    finally:
        t.close()


def test_smoke_handles_selector_breakage_error(tmp_path, monkeypatch):
    mod = _load_smoke_module()
    rec = _make_record(url="https://www.linkedin.com/jobs/view/4")
    client = _StubClient([rec], raise_breakage=True)
    _wire_smoke(mod, monkeypatch, tmp_path, client)

    rc = mod.main(["--profile", "p"])
    assert rc == 2

    stop = (
        PROJECT_ROOT / "scripts" / "output" / "spec_a2_stop_reason.md"
    )
    assert stop.exists()
    text = stop.read_text(encoding="utf-8")
    assert "SelectorBreakageError" in text or "miss rate" in text


def test_smoke_writes_summary_log(tmp_path, monkeypatch):
    mod = _load_smoke_module()
    rec = _make_record(url="https://www.linkedin.com/jobs/view/5")
    client = _StubClient([rec])
    _wire_smoke(mod, monkeypatch, tmp_path, client)

    log_file = tmp_path / "summary.log"
    rc = mod.main([
        "--profile", "p", "--log-file", str(log_file),
    ])
    assert rc == 0
    assert log_file.exists()
    contents = log_file.read_text(encoding="utf-8")
    assert "LinkedIn smoke summary" in contents
    assert "hiring_team_persisted" in contents
    assert "new:" in contents


def test_smoke_respects_max_keywords_argument(tmp_path, monkeypatch):
    mod = _load_smoke_module()
    # Synthetic client with multiple keywords in config.
    rec = _make_record(url="https://www.linkedin.com/jobs/view/6")
    client = _StubClient([rec])
    client.config.keywords = ["k1", "k2", "k3", "k4", "k5"]
    _wire_smoke(mod, monkeypatch, tmp_path, client)

    rc = mod.main(["--profile", "p", "--max-keywords", "2"])
    assert rc == 0
    # --max-keywords trimmed the in-memory config in place.
    assert client.config.keywords == ["k1", "k2"]
