"""Tests for scripts.load_eval_labels.

Parses markdown verdicts and inserts into eval_labels."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts import load_eval_labels as lel
from engine.persistence.tracker import Tracker


# ----- Parser tests -------------------------------------------------------

def test_parse_labels_finds_shortlist():
    md = """## 1. Acme -- PM

- **opp_id:** `42`

**Verdict:**
- [x] Would shortlist
- [ ] Would skip
- [ ] Unsure
"""
    posts = lel.parse_labels(md)
    assert len(posts) == 1
    assert posts[0]["opp_id"] == 42
    assert posts[0]["verdict"] == "shortlist"


def test_parse_labels_finds_skip():
    md = """## 1. Acme -- PM

- **opp_id:** `7`

**Verdict:**
- [ ] Would shortlist
- [x] Would skip
- [ ] Unsure
"""
    posts = lel.parse_labels(md)
    assert posts[0]["verdict"] == "skip"


def test_parse_labels_finds_unsure():
    md = """## 1. Acme -- PM

- **opp_id:** `9`

**Verdict:**
- [ ] Would shortlist
- [ ] Would skip
- [x] Unsure
"""
    posts = lel.parse_labels(md)
    assert posts[0]["verdict"] == "unsure"


def test_parse_labels_no_verdict_checked():
    md = """## 1. Acme -- PM

- **opp_id:** `1`

**Verdict:**
- [ ] Would shortlist
- [ ] Would skip
- [ ] Unsure
"""
    posts = lel.parse_labels(md)
    assert posts[0]["verdict"] is None


def test_parse_labels_multiple_postings():
    md = """## 1. A -- T1

- **opp_id:** `10`

**Verdict:**
- [x] Would shortlist
- [ ] Would skip
- [ ] Unsure

## 2. B -- T2

- **opp_id:** `20`

**Verdict:**
- [ ] Would shortlist
- [x] Would skip
- [ ] Unsure
"""
    posts = lel.parse_labels(md)
    assert len(posts) == 2
    assert posts[0]["opp_id"] == 10
    assert posts[0]["verdict"] == "shortlist"
    assert posts[1]["opp_id"] == 20
    assert posts[1]["verdict"] == "skip"


# ----- main() integration test -------------------------------------------

def test_main_inserts_labels(tmp_path, monkeypatch):
    # Set up a fake tracker DB.
    fake_db = tmp_path / "tracker.db"
    seeder = Tracker("test", db_path=fake_db)
    try:
        cid = seeder.upsert_company(name="Acme")
        a, _ = seeder.insert_opportunity(
            company_id=cid, source="x",
            source_url="https://x/1", title="T1",
        )
        b, _ = seeder.insert_opportunity(
            company_id=cid, source="x",
            source_url="https://x/2", title="T2",
        )
    finally:
        seeder.close()

    # Build a fake markdown file.
    md_path = tmp_path / "labels.md"
    md_path.write_text(
        f"""## 1. Acme -- T1

- **opp_id:** `{a}`

**Verdict:**
- [x] Would shortlist
- [ ] Would skip
- [ ] Unsure

## 2. Acme -- T2

- **opp_id:** `{b}`

**Verdict:**
- [ ] Would shortlist
- [x] Would skip
- [ ] Unsure
""",
        encoding="utf-8",
    )

    real_tracker_cls = lel.Tracker

    def make_tracker(profile_id):
        return real_tracker_cls(profile_id, db_path=fake_db)
    monkeypatch.setattr(lel, "Tracker", make_tracker)

    import argparse
    monkeypatch.setattr(
        lel, "parse_args",
        lambda: argparse.Namespace(file=str(md_path)),
    )

    lel.main()

    # Verify both got loaded.
    t = real_tracker_cls("test", db_path=fake_db)
    try:
        rows = t.list_eval_labels()
    finally:
        t.close()
    assert len(rows) == 2
    by_id = {r["opportunity_id"]: r["verdict"] for r in rows}
    assert by_id[a] == "shortlist"
    assert by_id[b] == "skip"
