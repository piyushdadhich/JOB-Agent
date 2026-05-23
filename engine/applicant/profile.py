"""ApplicantProfile -- PII used to fill application forms.

Loaded from config/profiles/{profile_id}_applicant.yaml. The
file is gitignored. Falls back to fields' defaults so a partially
filled YAML doesn't crash the agent -- but the agent should warn
loudly when it sees blank required fields like email or phone.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass(frozen=True)
class Education:
    degree: str
    school: str = ""
    graduation_year: str = ""


@dataclass(frozen=True)
class Certification:
    name: str
    issuer: str = ""
    active: bool = True


@dataclass(frozen=True)
class Demographics:
    gender: str = "Prefer not to say"
    ethnicity: str = "Prefer not to say"
    veteran_status: str = "Prefer not to answer"
    disability: str = "Prefer not to disclose"


@dataclass(frozen=True)
class ApplicantProfile:
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    city: str = "Toronto"
    province: str = "Ontario"
    country: str = "Canada"
    postal_code: str = ""
    linkedin_url: str = ""
    portfolio_url: str = ""
    work_authorization: str = "Permanent Resident"
    requires_sponsorship: bool = False
    willing_to_relocate: bool = True
    relocation_cities: list[str] = field(default_factory=list)
    salary_expectation: str = "Competitive / Open to discussion"
    salary_min: int = 0
    salary_max: int = 0
    salary_currency: str = "CAD"
    total_years_experience: int = 0
    start_date: str = "Immediately"
    education: list[Education] = field(default_factory=list)
    certifications: list[Certification] = field(default_factory=list)
    demographics: Demographics = field(default_factory=Demographics)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def required_fields_missing(self) -> list[str]:
        """Fields that should be set before submitting an app."""
        out = []
        for f in ("first_name", "last_name", "email", "phone"):
            if not getattr(self, f):
                out.append(f)
        return out


def _profile_path(profile_id: str) -> Path:
    return (
        PROJECT_ROOT / "config" / "profiles"
        / f"{profile_id}_applicant.yaml"
    )


def load_applicant_profile(profile_id: str) -> ApplicantProfile:
    path = _profile_path(profile_id)
    if not path.exists():
        raise FileNotFoundError(
            f"Applicant profile not found at {path}. "
            f"Copy from the spec template and fill in your PII."
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    education = [
        Education(**e) for e in (data.pop("education", None) or [])
    ]
    certifications = [
        Certification(**c) for c in (data.pop("certifications", None) or [])
    ]
    demographics = Demographics(
        **(data.pop("demographics", None) or {})
    )
    return ApplicantProfile(
        education=education,
        certifications=certifications,
        demographics=demographics,
        **data,
    )
