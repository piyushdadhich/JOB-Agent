"""Tests for scripts/extract_inventory_skills.py.

Runs in venv\\ (no ML deps). SkillsExtractor is mocked by injecting
a fake module hierarchy into sys.modules before extract() runs its
lazy import. Tests do NOT touch the real Alex DB.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts import extract_inventory_skills as eis
from engine.persistence.tracker import Tracker


def _install_fake_extractor(mapped_skills):
    """Install fake ojd_daps_skills modules in sys.modules.

    Returns a teardown callable that restores prior state.
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
            return _FakeDoc(mapped_skills)

        def map_skills(self, doc):
            return None  # real impl mutates in place; nothing to do

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


def test_extract_returns_dict_with_skill_ids():
    mapped = [
        {"ojo_ner_skill": "Agile", "match_id": "abc-123",
         "match_skill": "agile", "match_score": 0.99,
         "match_type": "skill"},
        {"ojo_ner_skill": "JIRA", "ojo_skill_id": "5678",
         "match_skill": "JIRA", "match_score": 0.85,
         "match_type": "skill"},
    ]
    restore = _install_fake_extractor(mapped)
    try:
        result = eis.extract("esco", "some text")
    finally:
        restore()
    assert result["taxonomy"] == "esco"
    assert result["skill_count"] == 2
    assert result["skill_ids"] == ["abc-123", "5678"]
    assert result["mapped_skills"] == mapped
    assert isinstance(result["elapsed_sec"], float)


def test_extract_handles_predictions_fallback_shape():
    mapped = [
        {"ojo_ner_skill": "vague",
         "predictions": {"match_id": "PARENT-1"}},
        {"ojo_ner_skill": "still vague"},  # no id anywhere -> skipped
    ]
    restore = _install_fake_extractor(mapped)
    try:
        result = eis.extract("esco", "text")
    finally:
        restore()
    assert result["skill_count"] == 2  # mapped count includes both
    assert result["skill_ids"] == ["PARENT-1"]  # only one had an id


def test_write_report_creates_markdown_with_both_taxonomies(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(eis, "REPORT_PATH", tmp_path / "report.md")
    esco = {
        "taxonomy": "esco", "elapsed_sec": 1.5, "skill_count": 1,
        "skill_ids": ["e1"],
        "mapped_skills": [{
            "ojo_ner_skill": "Agile", "match_id": "e1",
            "match_skill": "agile", "match_score": 0.99,
            "match_type": "skill",
        }],
    }
    lightcast = {
        "taxonomy": "lightcast", "elapsed_sec": 1.2, "skill_count": 1,
        "skill_ids": ["l1"],
        "mapped_skills": [{
            "ojo_ner_skill": "JIRA", "match_id": "l1",
            "match_skill": "jira", "match_score": 0.95,
            "match_type": "skill",
        }],
    }
    eis.write_report(esco, lightcast)
    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "ESCO" in text
    assert "Lightcast" in text
    assert "Agile" in text
    assert "JIRA" in text
    assert "e1" in text
    assert "l1" in text


def test_write_report_handles_empty_skill_lists(tmp_path, monkeypatch):
    monkeypatch.setattr(eis, "REPORT_PATH", tmp_path / "report.md")
    empty = {
        "taxonomy": "esco", "elapsed_sec": 0.1, "skill_count": 0,
        "skill_ids": [], "mapped_skills": [],
    }
    eis.write_report(empty, empty)
    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "ESCO mapped skills: 0" in text
    assert "Lightcast mapped skills: 0" in text


def test_main_persists_both_taxonomies(tmp_path, monkeypatch):
    fake_inventory = tmp_path / "inv.md"
    fake_inventory.write_text("dummy text", encoding="utf-8")
    fake_db = tmp_path / "tracker.db"

    monkeypatch.setattr(eis, "INVENTORY_PATH", fake_inventory)
    monkeypatch.setattr(eis, "REPORT_PATH", tmp_path / "report.md")

    def fake_extract(taxonomy, text, **kwargs):
        return {
            "taxonomy": taxonomy,
            "elapsed_sec": 0.1,
            "skill_count": 1,
            "skill_ids": [f"{taxonomy}-1"],
            "mapped_skills": [{
                "ojo_ner_skill": "x",
                "match_id": f"{taxonomy}-1",
                "match_skill": "x",
                "match_score": 1.0,
                "match_type": "skill",
            }],
        }
    monkeypatch.setattr(eis, "extract", fake_extract)

    real_tracker_cls = eis.Tracker

    def make_tracker(profile_id):
        return real_tracker_cls(profile_id, db_path=fake_db)
    monkeypatch.setattr(eis, "Tracker", make_tracker)

    eis.main()

    t = real_tracker_cls("default", db_path=fake_db)
    try:
        esco = t.get_profile_skills("default", "esco")
        lightcast = t.get_profile_skills("default", "lightcast")
        assert esco is not None
        assert lightcast is not None
        assert esco["skill_ids"] == ["esco-1"]
        assert lightcast["skill_ids"] == ["lightcast-1"]
    finally:
        t.close()
