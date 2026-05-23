"""Unit tests for engine.applicant.profile."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.applicant import profile as mod  # noqa: E402
from engine.applicant.profile import (  # noqa: E402
    ApplicantProfile,
    Certification,
    Demographics,
    Education,
    load_applicant_profile,
)


SAMPLE = {
    "first_name": "Alex", "last_name": "Doe",
    "email": "p@example.com", "phone": "555-0100",
    "city": "Toronto",
    "education": [
        {
            "degree": "MBA", "school": "Fordham",
            "graduation_year": "2018",
        },
    ],
    "certifications": [{"name": "PMP", "active": True}],
    "demographics": {"gender": "Prefer not to say"},
}


def _write(tmp_path, monkeypatch, profile_id, data):
    monkeypatch.setattr(mod, "PROJECT_ROOT", tmp_path)
    cfg_dir = tmp_path / "config" / "profiles"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / f"{profile_id}_applicant.yaml").write_text(
        yaml.safe_dump(data), encoding="utf-8",
    )


def test_load_full_profile(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, "default", SAMPLE)
    p = load_applicant_profile("default")
    assert p.first_name == "Alex"
    assert p.full_name == "Alex Doe"
    assert p.education[0].degree == "MBA"
    assert p.certifications[0].name == "PMP"
    assert p.demographics.gender == "Prefer not to say"


def test_missing_file_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "PROJECT_ROOT", tmp_path)
    with pytest.raises(FileNotFoundError):
        load_applicant_profile("nope")


def test_required_fields_missing_flags_blanks():
    p = ApplicantProfile()
    assert set(p.required_fields_missing()) == {
        "first_name", "last_name", "email", "phone",
    }


def test_required_fields_when_filled_returns_empty():
    p = ApplicantProfile(
        first_name="A", last_name="B",
        email="a@b.com", phone="555",
    )
    assert p.required_fields_missing() == []


def test_partial_yaml_uses_defaults(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, "default", {"first_name": "Pi"})
    p = load_applicant_profile("default")
    assert p.first_name == "Pi"
    assert p.work_authorization == "Permanent Resident"
    assert p.demographics.gender == "Prefer not to say"


def test_new_numeric_fields_default_to_zero_or_cad():
    p = ApplicantProfile()
    assert p.salary_min == 0
    assert p.salary_max == 0
    assert p.salary_currency == "CAD"
    assert p.total_years_experience == 0


def test_new_numeric_fields_load_from_yaml(tmp_path, monkeypatch):
    data = dict(SAMPLE)
    data.update({
        "salary_min": 95000,
        "salary_max": 145000,
        "salary_currency": "CAD",
        "total_years_experience": 13,
    })
    _write(tmp_path, monkeypatch, "default", data)
    p = load_applicant_profile("default")
    assert p.salary_min == 95000
    assert p.salary_max == 145000
    assert p.salary_currency == "CAD"
    assert p.total_years_experience == 13


def test_old_yaml_without_new_fields_still_loads(tmp_path, monkeypatch):
    # Backwards compat: a default_applicant.yaml without the new fields
    # must still load cleanly with defaults.
    _write(tmp_path, monkeypatch, "default", SAMPLE)
    p = load_applicant_profile("default")
    assert p.salary_min == 0
    assert p.total_years_experience == 0
