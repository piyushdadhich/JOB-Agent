"""Tests for scripts/backfill_opportunity_skills.py.

Runs in venv\\ (no ML deps). SkillsExtractor is mocked by injecting
a fake module hierarchy into sys.modules before main() runs its
lazy import. Tests do NOT touch the real Alex DB.
"""
from __future__ import annotations

import argparse
import json
import sys
import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts import backfill_opportunity_skills as bos
from engine.persistence.tracker import Tracker


# ----- Fake SkillsExtractor -----------------------------------------------

def _install_fake_extractor(mapped_per_taxonomy):
    """Install fake ojd_daps_skills modules in sys.modules.

    mapped_per_taxonomy is a dict {taxonomy_name: list_of_mapped_dicts}
    that the fake SkillsExtractor returns from get_skills regardless
    of input text. Returns a teardown callable.
    """
    saved = {}
    for name in (
        "ojd_daps_skills",
        "ojd_daps_skills.extract_skills",
        "ojd_daps_skills.extract_skills.extract_skills",
    ):
        saved[name] = sys.modules.get(name)

    fake_pkg = types.ModuleType("ojd_daps_skills")
    fake_extract_skills = types.ModuleType(
        "ojd_daps_skills.extract_skills"
    )
    fake_module = types.ModuleType(
        "ojd_daps_skills.extract_skills.extract_skills"
    )

    class _Underscore:
        def __init__(self, ms):
            self.mapped_skills = ms

    class _FakeDoc:
        def __init__(self, ms):
            self._ = _Underscore(ms)

    class FakeSkillsExtractor:
        def __init__(self, taxonomy_name):
            self.taxonomy_name = taxonomy_name

        def get_skills(self, text):
            return _FakeDoc(
                mapped_per_taxonomy.get(self.taxonomy_name, [])
            )

        def map_skills(self, doc):
            return None

    fake_module.SkillsExtractor = FakeSkillsExtractor
    sys.modules["ojd_daps_skills"] = fake_pkg
    sys.modules["ojd_daps_skills.extract_skills"] = fake_extract_skills
    sys.modules[
        "ojd_daps_skills.extract_skills.extract_skills"
    ] = fake_module

    def restore():
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod

    return restore


class _SimpleSm:
    """Minimal stand-in for SkillsExtractor for unit-testing
    extract_filtered_skill_ids in isolation (without going through
    the fake module install)."""
    def __init__(self, mapped):
        self._mapped = mapped

    def get_skills(self, text):
        outer = self

        class _U:
            mapped_skills = outer._mapped

        class _D:
            _ = _U()

        return _D()

    def map_skills(self, doc):
        return None


# ----- extract_filtered_skill_ids tests -----------------------------------

def test_extract_filtered_skill_ids_drops_below_floor():
    mapped = [
        {"match_id": "low", "match_score": 0.20,
         "match_type": "most_common_level_3"},
        {"match_id": "high", "match_score": 0.80, "match_type": "skill"},
    ]
    sm = _SimpleSm(mapped)
    ids, stats, labels = bos.extract_filtered_skill_ids(
        sm, "txt", 0.30, "lightcast"
    )
    assert ids == ["high"]
    assert stats["kept_count"] == 1
    assert stats["dropped_count"] == 1
    assert labels == [("high", "?", "lightcast", "skill")]


def test_extract_filtered_skill_ids_keeps_at_or_above_floor():
    # Score exactly at the floor must be kept (>= 0.30).
    mapped = [
        {"match_id": "edge", "match_score": 0.30,
         "match_skill": "Edge", "match_type": "skill"},
    ]
    sm = _SimpleSm(mapped)
    ids, stats, labels = bos.extract_filtered_skill_ids(
        sm, "txt", 0.30, "lightcast"
    )
    assert ids == ["edge"]
    assert stats["kept_count"] == 1
    assert stats["dropped_count"] == 0
    assert labels == [("edge", "Edge", "lightcast", "skill")]


def test_extract_filtered_skill_ids_handles_predictions_fallback():
    # Top-level match_id missing; predictions dict carries the id.
    # match_type contains "most_common_level" -> fallback_count++.
    mapped = [
        {"predictions": {"match_id": "PARENT-1"},
         "match_skill": "parent",
         "match_score": 0.50,
         "match_type": "most_common_level_2"},
    ]
    sm = _SimpleSm(mapped)
    ids, stats, labels = bos.extract_filtered_skill_ids(
        sm, "txt", 0.30, "esco"
    )
    assert ids == ["PARENT-1"]
    assert stats["kept_count"] == 1
    assert stats["fallback_count"] == 1
    assert labels == [
        ("PARENT-1", "parent", "esco", "most_common_level_2"),
    ]


def test_extract_filtered_skill_ids_returns_stats():
    mapped = [
        {"match_id": "a", "match_score": 0.99, "match_type": "skill"},
        {"match_id": "b", "match_score": 0.10, "match_type": "skill"},
        {"predictions": {"match_id": "c"},
         "match_score": 0.55,
         "match_type": "most_common_level_3"},
        {"match_score": 0.99},  # no id anywhere -> dropped
    ]
    sm = _SimpleSm(mapped)
    ids, stats, labels = bos.extract_filtered_skill_ids(
        sm, "txt", 0.30, "lightcast"
    )
    assert ids == ["a", "c"]
    assert stats["mapped_total"] == 4
    assert stats["kept_count"] == 2
    assert stats["dropped_count"] == 2  # one below floor + one no id
    assert stats["fallback_count"] == 1
    # Two label tuples (one per kept skill).
    assert len(labels) == 2
    assert labels[0][0] == "a"
    assert labels[1][0] == "c"


# ----- main() happy-path test ---------------------------------------------

def test_main_persists_via_dual_method(tmp_path, monkeypatch):
    # Set up a temp tracker DB seeded with one opportunity that has
    # plenty of posting_text.
    fake_db = tmp_path / "tracker.db"
    seeder = Tracker("test", db_path=fake_db)
    try:
        cid = seeder.upsert_company(name="Acme")
        opp_id, _ = seeder.insert_opportunity(
            company_id=cid, source="jobspy",
            source_url="https://x/1", title="PM",
            posting_text="x" * 500,
        )
    finally:
        seeder.close()

    # Patch Tracker constructor inside the script module so it points
    # at our temp DB regardless of profile_id.
    real_tracker_cls = bos.Tracker

    def make_tracker(profile_id):
        return real_tracker_cls(profile_id, db_path=fake_db)
    monkeypatch.setattr(bos, "Tracker", make_tracker)

    # Patch argparse.parse_args to inject our chosen flags.
    monkeypatch.setattr(
        bos, "parse_args",
        lambda: argparse.Namespace(
            confidence_floor=0.30,
            min_score=0.30,
            exclude_type=[],
            min_text_length=200,
            primary="lightcast",
            secondary="esco",
            limit=None,
            dry_run=False,
        ),
    )

    mapped_per_tax = {
        "lightcast": [
            {"match_id": "LC-keep", "match_skill": "Java",
             "match_score": 0.80, "match_type": "skill"},
            {"match_id": "LC-drop", "match_skill": "Drop",
             "match_score": 0.20, "match_type": "skill"},
        ],
        "esco": [
            {"match_id": "ESCO-keep", "match_skill": "Python",
             "match_score": 0.50, "match_type": "skill"},
        ],
    }
    restore = _install_fake_extractor(mapped_per_tax)
    try:
        bos.main()
    finally:
        restore()

    # Verify both columns persisted via the dual method.
    t = real_tracker_cls("test", db_path=fake_db)
    try:
        row = t._query_one(
            "SELECT extracted_skill_ids, "
            "       extracted_skill_ids_secondary, "
            "       secondary_taxonomy "
            "FROM opportunities WHERE id = ?",
            (opp_id,),
        )
        # Verify skill labels were persisted alongside.
        labels = t.get_skill_labels(["LC-keep", "ESCO-keep", "LC-drop"])
    finally:
        t.close()
    assert json.loads(row["extracted_skill_ids"]) == ["LC-keep"]
    assert json.loads(row["extracted_skill_ids_secondary"]) == ["ESCO-keep"]
    assert row["secondary_taxonomy"] == "esco"
    assert labels["LC-keep"] == "Java"
    assert labels["ESCO-keep"] == "Python"
    # Below-floor skill should NOT be in skill_labels (filtered out
    # before the upsert call).
    assert labels["LC-drop"] == "<unknown>"


def test_populate_inventory_labels_loads_from_profile_skills(tmp_path):
    fake_db = tmp_path / "tracker.db"
    t = Tracker("default", db_path=fake_db)
    try:
        # Simulate inventory extraction output landing in
        # profile_skills.raw_extraction.
        raw = json.dumps([
            {"match_id": "INV-LC-1", "match_skill": "Inventory Skill 1",
             "match_score": 0.90, "match_type": "skill"},
            {"predictions": {"match_id": "INV-LC-2"},
             "match_skill": "Inventory Skill 2",
             "match_score": 0.50,
             "match_type": "most_common_level_3"},
            {"match_id": "INV-LC-3"},  # missing label -> skipped
        ])
        t.upsert_profile_skills(
            profile_id="default", taxonomy="lightcast",
            skill_ids=["INV-LC-1", "INV-LC-2"],
            source_doc="career_inventory.md",
            raw_extraction=raw,
        )
        count = bos.populate_inventory_labels(t)
        # Two label entries with both id and label; the third
        # entry has no label and is skipped.
        assert count == 2
        labels = t.get_skill_labels(
            ["INV-LC-1", "INV-LC-2", "INV-LC-3"]
        )
        assert labels["INV-LC-1"] == "Inventory Skill 1"
        assert labels["INV-LC-2"] == "Inventory Skill 2"
        assert labels["INV-LC-3"] == "<unknown>"
    finally:
        t.close()


# ----- Spec B2: exclude_types + min_score plumbing tests ------------------

def test_extract_filtered_skill_ids_drops_excluded_match_type():
    """A high-confidence match with an excluded match_type must be
    dropped (not just low-confidence)."""
    mapped = [
        {"match_id": "30.0.613.0", "match_skill": "Biology",
         "match_score": 0.85, "match_type": "most_common_level_1"},
        {"match_id": "KS_real", "match_skill": "Java",
         "match_score": 0.85, "match_type": "skill"},
    ]
    sm = _SimpleSm(mapped)
    ids, stats, labels = bos.extract_filtered_skill_ids(
        sm, "txt", 0.50, "lightcast",
        exclude_types={"most_common_level_1"},
    )
    assert ids == ["KS_real"]
    assert stats["kept_count"] == 1
    assert stats["dropped_count"] == 1
    assert stats["dropped_type"] == 1
    # Excluded skill should NOT show up in labels.
    assert labels == [("KS_real", "Java", "lightcast", "skill")]


def test_extract_filtered_skill_ids_empty_exclude_keeps_level_1():
    mapped = [
        {"match_id": "30.0.613.0", "match_skill": "Biology",
         "match_score": 0.85, "match_type": "most_common_level_1"},
    ]
    sm = _SimpleSm(mapped)
    ids, _, _ = bos.extract_filtered_skill_ids(
        sm, "txt", 0.50, "lightcast", exclude_types=set(),
    )
    assert ids == ["30.0.613.0"]


def test_parse_args_defaults_to_spec_b2_filters(monkeypatch):
    """parse_args() with no flags should default to min_score=0.2 and
    exclude_type=['most_common_level_1'] per Spec B2-retune step 2
    (lowered 0.5 -> 0.3 -> 0.2; the 0.5 and 0.3 runs each produced
    99.2% SKIP because the type filter dominates inventory size)."""
    monkeypatch.setattr(sys, "argv", ["backfill"])
    args = bos.parse_args()
    assert args.confidence_floor == 0.20
    assert args.min_score == 0.20
    assert args.exclude_type == ["most_common_level_1"]


def test_parse_args_min_score_overrides_confidence_floor(monkeypatch):
    """--min-score is the canonical name; if supplied with
    --confidence-floor, --min-score wins (it's resolved AFTER)."""
    monkeypatch.setattr(
        sys, "argv",
        ["backfill", "--confidence-floor", "0.3", "--min-score", "0.7"],
    )
    args = bos.parse_args()
    assert args.confidence_floor == 0.70
    assert args.min_score == 0.70


def test_parse_args_exclude_type_repeatable(monkeypatch):
    monkeypatch.setattr(
        sys, "argv",
        [
            "backfill",
            "--exclude-type", "most_common_level_1",
            "--exclude-type", "most_common_level_2",
        ],
    )
    args = bos.parse_args()
    assert args.exclude_type == [
        "most_common_level_1",
        "most_common_level_2",
    ]


def test_main_respects_spec_b2_filters_end_to_end(tmp_path, monkeypatch):
    """Full main() path: parse_args defaults (min_score=0.5,
    exclude_type=['most_common_level_1']) must filter both
    primary and secondary extractions."""
    fake_db = tmp_path / "tracker.db"
    seeder = Tracker("test", db_path=fake_db)
    try:
        cid = seeder.upsert_company(name="Acme")
        opp_id, _ = seeder.insert_opportunity(
            company_id=cid, source="jobspy",
            source_url="https://x/1", title="PM",
            posting_text="x" * 500,
        )
    finally:
        seeder.close()

    real_tracker_cls = bos.Tracker

    def make_tracker(profile_id):
        return real_tracker_cls(profile_id, db_path=fake_db)
    monkeypatch.setattr(bos, "Tracker", make_tracker)
    monkeypatch.setattr(sys, "argv", ["backfill"])  # use defaults

    mapped_per_tax = {
        "lightcast": [
            # Excluded by type (level_1) even though score is high.
            {"match_id": "LC-broad", "match_skill": "Biology",
             "match_score": 0.90, "match_type": "most_common_level_1"},
            # Dropped by min_score (0.2 default per Spec B2-retune step 2).
            {"match_id": "LC-weak", "match_skill": "Weak",
             "match_score": 0.10, "match_type": "skill"},
            # Kept.
            {"match_id": "LC-good", "match_skill": "Java",
             "match_score": 0.80, "match_type": "skill"},
        ],
        "esco": [
            {"match_id": "ESCO-good", "match_skill": "Python",
             "match_score": 0.80, "match_type": "skill"},
        ],
    }
    restore = _install_fake_extractor(mapped_per_tax)
    try:
        bos.main()
    finally:
        restore()

    t = real_tracker_cls("test", db_path=fake_db)
    try:
        row = t._query_one(
            "SELECT extracted_skill_ids, extracted_skill_ids_secondary "
            "FROM opportunities WHERE id = ?",
            (opp_id,),
        )
    finally:
        t.close()
    assert json.loads(row["extracted_skill_ids"]) == ["LC-good"]
    assert json.loads(row["extracted_skill_ids_secondary"]) == ["ESCO-good"]
