"""Tests for engine/matching/skill_extractor.py.

Run in venv\\ (no ML deps). SkillsExtractor is stubbed by injecting
a fake module hierarchy into sys.modules before _ensure_loaded fires.
Tests do NOT touch the real Alex DB and do NOT load ojd_daps_skills.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest  # noqa: F401  (autouse fixtures may rely on it)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.matching.skill_extractor import SkillExtractorWrapper  # noqa: E402


def _install_fake_extractor(mapped_skills):
    """Install fake ojd_daps_skills modules into sys.modules.

    Returns a teardown callable.
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
            return _FakeDoc(list(mapped_skills))

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


# Reusable fixture-style sample mapped_skills.

_MIXED_MAPPED = [
    {
        "ojo_skill": "SAP",
        "match_id": "KS_SAP",
        "match_skill": "SAP",
        "match_score": 0.99,
        "match_type": "skill",
    },
    {
        "ojo_skill": "leadership",
        "match_id": "KS_LEAD",
        "match_skill": "Leadership",
        "match_score": 0.40,  # below 0.5
        "match_type": "skill",
    },
    {
        "ojo_skill": "broad-category-match",
        "match_id": "30.0.613.0",
        "match_skill": "Biology",
        "match_score": 0.70,
        "match_type": "most_common_level_1",  # excluded
    },
    {
        "ojo_skill": "agile",
        "match_id": "KS_AGILE",
        "match_skill": "Agile Methodology",
        "match_score": 0.85,
        "match_type": "skill",
    },
    {
        "ojo_skill": "vague",
        "predictions": {"match_id": "PARENT_X"},
        "match_score": 0.60,
        "match_type": "most_common_level_3",  # not excluded by default
    },
]


def test_default_excludes_most_common_level_1():
    restore = _install_fake_extractor(_MIXED_MAPPED)
    try:
        w = SkillExtractorWrapper()  # defaults
        ids = w.extract_skill_ids("anything")
    finally:
        restore()
    # Biology (most_common_level_1) and Leadership (score<0.5) dropped;
    # SAP (0.99 skill) + Agile (0.85 skill) + PARENT_X (0.60 level_3) kept.
    assert "KS_SAP" in ids
    assert "KS_AGILE" in ids
    assert "PARENT_X" in ids
    assert "30.0.613.0" not in ids
    assert "KS_LEAD" not in ids


def test_min_score_filters_low_confidence():
    restore = _install_fake_extractor(_MIXED_MAPPED)
    try:
        w = SkillExtractorWrapper(min_score=0.5, exclude_types=set())
        ids = w.extract_skill_ids("anything")
    finally:
        restore()
    # With exclude_types empty, Biology (0.70) is back in; only
    # Leadership (0.40) drops.
    assert "KS_LEAD" not in ids
    assert "30.0.613.0" in ids
    assert "KS_SAP" in ids


def test_exclude_types_filters_broad_categories():
    restore = _install_fake_extractor(_MIXED_MAPPED)
    try:
        # min_score=0 so only the type filter operates.
        w = SkillExtractorWrapper(
            min_score=0.0,
            exclude_types={"most_common_level_1", "most_common_level_3"},
        )
        ids = w.extract_skill_ids("anything")
    finally:
        restore()
    assert "30.0.613.0" not in ids   # level_1 excluded
    assert "PARENT_X" not in ids     # level_3 excluded
    assert "KS_SAP" in ids
    assert "KS_LEAD" in ids          # low score allowed when min=0
    assert "KS_AGILE" in ids


def test_unfiltered_returns_all():
    restore = _install_fake_extractor(_MIXED_MAPPED)
    try:
        w = SkillExtractorWrapper.unfiltered()
        ids = w.extract_skill_ids("anything")
    finally:
        restore()
    assert set(ids) == {
        "KS_SAP", "KS_LEAD", "30.0.613.0", "KS_AGILE", "PARENT_X",
    }


def test_extract_detailed_returns_filtered_dicts():
    restore = _install_fake_extractor(_MIXED_MAPPED)
    try:
        w = SkillExtractorWrapper()
        out = w.extract_detailed("anything")
    finally:
        restore()
    # All returned items must be dicts and pass the filter
    assert all(isinstance(m, dict) for m in out)
    surface = {m.get("ojo_skill") for m in out}
    assert "SAP" in surface
    assert "agile" in surface
    assert "broad-category-match" not in surface
    assert "leadership" not in surface


def test_lazy_load_extractor_only_on_first_use():
    restore = _install_fake_extractor(_MIXED_MAPPED)
    try:
        w = SkillExtractorWrapper()
        assert w._extractor is None
        w.extract_skill_ids("anything")
        assert w._extractor is not None
    finally:
        restore()


def test_handles_skill_with_no_id_gracefully():
    bad_mapped = [
        {"ojo_skill": "no id here", "match_score": 0.99,
         "match_type": "skill"},  # passes filter but has no id
        {
            "ojo_skill": "valid",
            "match_id": "KS_OK",
            "match_score": 0.99,
            "match_type": "skill",
        },
    ]
    restore = _install_fake_extractor(bad_mapped)
    try:
        w = SkillExtractorWrapper()
        ids = w.extract_skill_ids("anything")
    finally:
        restore()
    assert ids == ["KS_OK"]


def test_match_score_non_numeric_treated_as_zero():
    weird_mapped = [
        {
            "ojo_skill": "x",
            "match_id": "KS_X",
            "match_score": None,
            "match_type": "skill",
        },
        {
            "ojo_skill": "y",
            "match_id": "KS_Y",
            "match_score": "0.99",
            "match_type": "skill",
        },
    ]
    restore = _install_fake_extractor(weird_mapped)
    try:
        w = SkillExtractorWrapper()
        ids = w.extract_skill_ids("anything")
    finally:
        restore()
    # None and "0.99" both treated as 0 -> below 0.5 -> dropped
    assert ids == []
