"""Tests for engine.profiles.loader.

Most tests use tmp_path with synthetic YAML fixtures so they don't
couple to live config/. A small integration block at the end verifies
the real config/domains/corporate.yaml + config/profiles/default.yaml
parse correctly.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.profiles.loader import (
    Domain,
    Profile,
    RoleType,
    load_domain,
    load_profile,
    load_profile_from_default,
)


MIN_DOMAIN_YAML = textwrap.dedent(
    """
    domain: corporate
    role_types:
      delivery_manager:
        description: "Owns delivery"
        search_terms:
          - "delivery manager"
          - "delivery lead"
        typical_seniority: [mid, senior]
      scrum_master:
        description: "Facilitates scrum"
        search_terms:
          - "scrum master"
        typical_seniority: [mid]
    red_flag_phrases:
      - "fast-paced"
      - "rockstar"
    title_disambiguation:
      construction_indicators:
        employers: ["PCL Construction"]
        title_keywords: ["site"]
    """
).strip()


MIN_PROFILE_YAML = textwrap.dedent(
    """
    profile_id: tester
    display_name: Tester McTest
    domain: corporate
    target_cities: [toronto, calgary]
    target_role_types:
      - delivery_manager
      - scrum_master
    target_sectors_by_city:
      toronto: [public_sector, financial_services]
      calgary: [public_sector, energy]
    salary:
      floor: 80000
      target: 95000
      cap: 120000
    employer_size_priority: mid_sized
    inventory: source_materials/tester/career_inventory.md
    exclusions: config/exclusions/tester.yaml
    sources_enabled: [jobspy, greenhouse_api]
    jobspy:
      sites: [indeed]
      hours_old: 48
      results_per_search: 25
      rate_limit_seconds: 3
      exact_phrase: true
    """
).strip()


def _write_config(root: Path, domain_yaml: str, profile_yaml: str | None,
                  domain_name: str = "corporate",
                  profile_id: str = "tester") -> Path:
    (root / "domains").mkdir(parents=True, exist_ok=True)
    (root / "profiles").mkdir(parents=True, exist_ok=True)
    (root / "domains" / f"{domain_name}.yaml").write_text(
        domain_yaml, encoding="utf-8"
    )
    if profile_yaml is not None:
        (root / "profiles" / f"{profile_id}.yaml").write_text(
            profile_yaml, encoding="utf-8"
        )
    return root


# ----- Domain loading ------------------------------------------------------

def test_load_domain_corporate_returns_domain_object(tmp_path):
    root = _write_config(tmp_path, MIN_DOMAIN_YAML, None)
    domain = load_domain("corporate", config_root=root)
    assert isinstance(domain, Domain)
    assert domain.domain == "corporate"
    assert "delivery_manager" in domain.role_types
    assert "scrum_master" in domain.role_types


def test_load_domain_role_types_parsed_correctly(tmp_path):
    root = _write_config(tmp_path, MIN_DOMAIN_YAML, None)
    domain = load_domain("corporate", config_root=root)
    dm = domain.role_types["delivery_manager"]
    assert isinstance(dm, RoleType)
    assert dm.id == "delivery_manager"
    assert dm.description == "Owns delivery"
    assert dm.typical_seniority == ["mid", "senior"]


def test_role_type_search_terms_preserved_in_order(tmp_path):
    root = _write_config(tmp_path, MIN_DOMAIN_YAML, None)
    domain = load_domain("corporate", config_root=root)
    assert domain.role_types["delivery_manager"].search_terms == [
        "delivery manager",
        "delivery lead",
    ]


def test_red_flag_phrases_loaded(tmp_path):
    root = _write_config(tmp_path, MIN_DOMAIN_YAML, None)
    domain = load_domain("corporate", config_root=root)
    assert "fast-paced" in domain.red_flag_phrases
    assert "rockstar" in domain.red_flag_phrases


def test_title_disambiguation_loaded(tmp_path):
    root = _write_config(tmp_path, MIN_DOMAIN_YAML, None)
    domain = load_domain("corporate", config_root=root)
    construction = domain.title_disambiguation["construction_indicators"]
    assert "PCL Construction" in construction["employers"]
    assert "site" in construction["title_keywords"]


def test_load_domain_missing_file_raises_file_not_found(tmp_path):
    (tmp_path / "domains").mkdir()
    with pytest.raises(FileNotFoundError):
        load_domain("nonexistent", config_root=tmp_path)


def test_load_domain_invalid_schema_raises_value_error(tmp_path):
    bad = "domain: corporate\n# missing role_types"
    root = _write_config(tmp_path, bad, None)
    with pytest.raises(ValueError, match="role_types"):
        load_domain("corporate", config_root=root)


def test_load_domain_role_type_missing_search_terms_raises(tmp_path):
    bad = textwrap.dedent(
        """
        domain: corporate
        role_types:
          delivery_manager:
            description: "no search terms"
        """
    ).strip()
    root = _write_config(tmp_path, bad, None)
    with pytest.raises(ValueError, match="search_terms"):
        load_domain("corporate", config_root=root)


# ----- Profile loading -----------------------------------------------------

def test_load_profile_default_returns_profile_object(tmp_path):
    root = _write_config(tmp_path, MIN_DOMAIN_YAML, MIN_PROFILE_YAML)
    profile = load_profile("tester", config_root=root)
    assert isinstance(profile, Profile)
    assert profile.profile_id == "tester"
    assert profile.display_name == "Tester McTest"
    assert profile.domain == "corporate"
    assert profile.target_cities == ["toronto", "calgary"]


def test_load_profile_target_role_types_resolved_to_role_type_objects(tmp_path):
    root = _write_config(tmp_path, MIN_DOMAIN_YAML, MIN_PROFILE_YAML)
    profile = load_profile("tester", config_root=root)
    assert all(isinstance(rt, RoleType) for rt in profile.target_role_types)
    ids = [rt.id for rt in profile.target_role_types]
    assert ids == ["delivery_manager", "scrum_master"]
    # search_terms came from domain, not profile
    assert profile.target_role_types[0].search_terms == [
        "delivery manager",
        "delivery lead",
    ]


def test_load_profile_unknown_role_type_raises_value_error(tmp_path):
    bad_profile = MIN_PROFILE_YAML.replace(
        "  - scrum_master", "  - made_up_role"
    )
    root = _write_config(tmp_path, MIN_DOMAIN_YAML, bad_profile)
    with pytest.raises(ValueError, match="made_up_role"):
        load_profile("tester", config_root=root)


def test_load_profile_source_config_extracted(tmp_path):
    root = _write_config(tmp_path, MIN_DOMAIN_YAML, MIN_PROFILE_YAML)
    profile = load_profile("tester", config_root=root)
    assert "jobspy" in profile.source_config
    cfg = profile.source_config["jobspy"]
    assert cfg["sites"] == ["indeed"]
    assert cfg["hours_old"] == 48
    assert cfg["results_per_search"] == 25
    assert cfg["exact_phrase"] is True
    # greenhouse_api was enabled but had no config block — should be absent
    assert "greenhouse_api" not in profile.source_config


def test_load_profile_target_sectors_by_city_parsed(tmp_path):
    root = _write_config(tmp_path, MIN_DOMAIN_YAML, MIN_PROFILE_YAML)
    profile = load_profile("tester", config_root=root)
    assert profile.target_sectors_by_city["toronto"] == [
        "public_sector",
        "financial_services",
    ]
    assert profile.target_sectors_by_city["calgary"] == [
        "public_sector",
        "energy",
    ]


def test_load_profile_salary_and_inventory_paths(tmp_path):
    root = _write_config(tmp_path, MIN_DOMAIN_YAML, MIN_PROFILE_YAML)
    profile = load_profile("tester", config_root=root)
    assert profile.salary == {"floor": 80000, "target": 95000, "cap": 120000}
    assert profile.employer_size_priority == "mid_sized"
    assert profile.inventory_path == \
        "source_materials/tester/career_inventory.md"
    assert profile.exclusions_path == "config/exclusions/tester.yaml"
    assert profile.sources_enabled == ["jobspy", "greenhouse_api"]


def test_load_profile_missing_file_raises_file_not_found(tmp_path):
    root = _write_config(tmp_path, MIN_DOMAIN_YAML, None)
    with pytest.raises(FileNotFoundError):
        load_profile("nonexistent", config_root=root)


def test_load_profile_references_unknown_domain_raises(tmp_path):
    profile_yaml = MIN_PROFILE_YAML.replace(
        "domain: corporate", "domain: ghost_domain"
    )
    root = _write_config(tmp_path, MIN_DOMAIN_YAML, profile_yaml)
    with pytest.raises(FileNotFoundError):
        load_profile("tester", config_root=root)


# ----- load_profile_from_default ------------------------------------------

def _live_profile_is_user_customised() -> bool:
    """True iff config/profiles/default.yaml has user values, not the
    .example placeholder marker (display_name: 'Your Name')."""
    import yaml
    p = PROJECT_ROOT / "config" / "profiles" / "default.yaml"
    if not p.exists():
        return False
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return False
    return data.get("display_name") not in (None, "", "Your Name")


def test_load_profile_from_default_uses_project_root():
    # Conftest seeds default.yaml from the .example template on a
    # fresh checkout; the template uses placeholder role_types like
    # 'your_role_type' which won't validate against the corporate
    # domain vocabulary. Skip when the live yaml hasn't been
    # customised yet (a real user run replaces the placeholders
    # via the setup wizard).
    if not _live_profile_is_user_customised():
        pytest.skip(
            "default.yaml is the .example template — onboarding "
            "wizard hasn't customised it yet."
        )
    profile = load_profile_from_default("default")
    assert isinstance(profile, Profile)
    assert profile.profile_id == "default"


# ----- Integration with real config ---------------------------------------

def test_real_corporate_domain_loads():
    domain = load_domain("corporate")
    assert domain.domain == "corporate"
    # Spec started with 10 role_types in corporate.yaml; the count
    # grows as the vocabulary expands. Assert the originals are
    # still present rather than pinning a specific total.
    assert len(domain.role_types) >= 10
    for required in [
        "delivery_manager", "scrum_master", "product_owner",
        "project_coordinator", "business_analyst", "land_agent",
        "sourcing_specialist", "operations_manager", "payments_officer",
        "project_controls",
    ]:
        assert required in domain.role_types, f"missing {required}"
    # red_flag_phrases minimum 8 per spec
    assert len(domain.red_flag_phrases) >= 8


def test_real_default_profile_loads_and_resolves():
    if not _live_profile_is_user_customised():
        pytest.skip(
            "default.yaml is the .example template — onboarding "
            "wizard hasn't customised it yet."
        )
    profile = load_profile_from_default("default")
    assert profile.domain == "corporate"
    assert "toronto" in profile.target_cities
    assert "remote_canada" in profile.target_cities
    assert len(profile.target_role_types) >= 10
    assert all(isinstance(rt, RoleType) for rt in profile.target_role_types)
    assert "jobspy" in profile.source_config
