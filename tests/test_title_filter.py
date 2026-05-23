"""Tests for engine.matching.title_filter (Spec B3 TASK 2)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.matching.title_filter import (  # noqa: E402
    is_title_excluded,
    load_title_exclusions,
)


# --- load_title_exclusions --------------------------------------------

def test_load_title_exclusions_from_yaml(tmp_path):
    path = tmp_path / "exclusions.yaml"
    path.write_text(yaml.safe_dump({
        "excluded_titles": ["warehouse associate", "Security Guard"],
    }), encoding="utf-8")
    out = load_title_exclusions(path)
    # Lowercased on load.
    assert out == ["warehouse associate", "security guard"]


def test_load_missing_file_returns_empty(tmp_path):
    out = load_title_exclusions(tmp_path / "nope.yaml")
    assert out == []


def test_load_empty_yaml_returns_empty(tmp_path):
    path = tmp_path / "exclusions.yaml"
    path.write_text("excluded_titles: []\n", encoding="utf-8")
    assert load_title_exclusions(path) == []


def test_load_real_project_yaml_returns_nonempty():
    """The committed config/title_exclusions.yaml should load with
    at least 30 patterns (sanity check for accidental wipe)."""
    out = load_title_exclusions()
    assert len(out) >= 30
    # Should include canonical examples.
    assert "warehouse associate" in out
    assert "software engineer" in out


# --- is_title_excluded ------------------------------------------------

def test_is_title_excluded_substring_match():
    excl = ["software engineer", "warehouse associate"]
    assert is_title_excluded("Senior Software Engineer", excl)
    assert is_title_excluded("Warehouse Associate II", excl)


def test_is_title_excluded_case_insensitive():
    excl = ["security guard"]
    assert is_title_excluded("SECURITY GUARD", excl)
    assert is_title_excluded("Security Guard, Overnight", excl)


def test_is_title_excluded_no_false_positive_on_project_manager():
    """'Software Project Manager' should NOT be excluded just
    because 'software engineer' is in the list. Substring matching
    on 'software engineer' won't fire on 'Software Project Manager'."""
    excl = ["software engineer"]
    assert not is_title_excluded("Software Project Manager", excl)
    assert not is_title_excluded("Senior Project Manager", excl)


def test_is_title_excluded_empty_title_returns_false():
    assert is_title_excluded("", ["software engineer"]) is False
    assert is_title_excluded(None, ["software engineer"]) is False


def test_is_title_excluded_empty_exclusions_returns_false():
    assert is_title_excluded("Software Engineer", []) is False


def test_is_title_excluded_match_anywhere_in_title():
    """Patterns can match mid-title, not just at the start."""
    excl = ["truck driver"]
    assert is_title_excluded("Class A Truck Driver - Toronto", excl)
