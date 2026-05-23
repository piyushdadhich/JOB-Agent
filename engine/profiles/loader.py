"""
Profile and Domain loader.

Per architecture Section 8.1/8.2: profiles live under config/profiles/{id}.yaml
and reference a shared domain vocabulary at config/domains/{domain}.yaml.
This module is the single entry point that hydrates those YAML files into
typed Profile/Domain/RoleType dataclasses for downstream consumers (Sources,
evaluators, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


@dataclass
class RoleType:
    """A single role vocabulary entry from a domain YAML."""
    id: str
    description: str
    search_terms: List[str]
    typical_seniority: List[str] = field(default_factory=list)


@dataclass
class Domain:
    """A domain vocabulary (e.g. corporate, teaching) shared across profiles."""
    domain: str
    role_types: Dict[str, RoleType]
    red_flag_phrases: List[str] = field(default_factory=list)
    title_disambiguation: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


@dataclass
class Profile:
    """A user profile with resolved role_types from its domain."""
    profile_id: str
    display_name: str
    domain: str
    target_cities: List[str]
    target_role_types: List[RoleType]
    target_sectors_by_city: Dict[str, List[str]] = field(default_factory=dict)
    salary: dict = field(default_factory=dict)
    employer_size_priority: str = ""
    inventory_path: str = ""
    exclusions_path: str = ""
    sources_enabled: List[str] = field(default_factory=list)
    source_config: Dict[str, dict] = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


def _project_root() -> Path:
    """Project root = parent of engine/ dir."""
    return Path(__file__).resolve().parent.parent.parent


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping at top level of {path}")
    return data


def _require(data: dict, key: str, source: Path) -> Any:
    if key not in data:
        raise ValueError(f"Missing required key '{key}' in {source}")
    return data[key]


def load_domain(domain_name: str, config_root: Optional[Path] = None) -> Domain:
    """Load and parse config/domains/{domain_name}.yaml -> Domain."""
    config_root = config_root or (_project_root() / "config")
    path = config_root / "domains" / f"{domain_name}.yaml"
    raw = _read_yaml(path)

    declared_name = _require(raw, "domain", path)
    role_types_raw = _require(raw, "role_types", path)
    if not isinstance(role_types_raw, dict):
        raise ValueError(f"'role_types' must be a mapping in {path}")

    role_types: Dict[str, RoleType] = {}
    for rid, rdef in role_types_raw.items():
        if not isinstance(rdef, dict):
            raise ValueError(
                f"role_type '{rid}' in {path} must be a mapping"
            )
        if "search_terms" not in rdef:
            raise ValueError(
                f"role_type '{rid}' in {path} missing 'search_terms'"
            )
        role_types[rid] = RoleType(
            id=rid,
            description=rdef.get("description", ""),
            search_terms=list(rdef.get("search_terms") or []),
            typical_seniority=list(rdef.get("typical_seniority") or []),
        )

    return Domain(
        domain=declared_name,
        role_types=role_types,
        red_flag_phrases=list(raw.get("red_flag_phrases") or []),
        title_disambiguation=dict(raw.get("title_disambiguation") or {}),
        raw=raw,
    )


def load_profile(
    profile_id: str, config_root: Optional[Path] = None
) -> Profile:
    """Load config/profiles/{profile_id}.yaml and resolve its domain."""
    config_root = config_root or (_project_root() / "config")
    path = config_root / "profiles" / f"{profile_id}.yaml"
    raw = _read_yaml(path)

    domain_name = _require(raw, "domain", path)
    domain = load_domain(domain_name, config_root)

    requested_role_ids = list(raw.get("target_role_types") or [])
    resolved: List[RoleType] = []
    for rid in requested_role_ids:
        if rid not in domain.role_types:
            raise ValueError(
                f"Profile {profile_id} references unknown role_type "
                f"'{rid}' (not in domain '{domain_name}')"
            )
        resolved.append(domain.role_types[rid])

    sources_enabled = list(raw.get("sources_enabled") or [])
    source_config: Dict[str, dict] = {}
    for src in sources_enabled:
        if src in raw and isinstance(raw[src], dict):
            source_config[src] = dict(raw[src])

    target_sectors_raw = raw.get("target_sectors_by_city") or {}
    target_sectors_by_city: Dict[str, List[str]] = {
        city: list(sectors or [])
        for city, sectors in target_sectors_raw.items()
    }

    return Profile(
        profile_id=raw.get("profile_id", profile_id),
        display_name=raw.get("display_name", profile_id),
        domain=domain_name,
        target_cities=list(raw.get("target_cities") or []),
        target_role_types=resolved,
        target_sectors_by_city=target_sectors_by_city,
        salary=dict(raw.get("salary") or {}),
        employer_size_priority=raw.get("employer_size_priority", ""),
        inventory_path=raw.get("inventory", ""),
        exclusions_path=raw.get("exclusions", ""),
        sources_enabled=sources_enabled,
        source_config=source_config,
        raw=raw,
    )


def load_profile_from_default(profile_id: str) -> Profile:
    """Load a profile using the default project-root config/ directory."""
    return load_profile(profile_id, config_root=_project_root() / "config")
