"""Spec D1 TASK 5 — Discovery Expansion orchestrator.

Runs all four Phase 7 expansion strategies and assembles a
single ExpansionReport. The report is HUMAN-FACING — nothing
auto-updates the profile, inventory, or watchlist. The user
reads the report, confirms specific suggestions, and runs
scripts/update_discovered_roles.py (and similar) for accepted
changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from engine.expansion.adjacent_employers import (
    AdjacentEmployerFinder, AdjacentSuggestion,
)
from engine.expansion.employer_deep import (
    DeepTarget, EmployerDeepPromoter,
)
from engine.expansion.inventory_gaps import (
    InventoryGapSurfer, SkillGap,
)
from engine.expansion.title_clusters import (
    TitleCluster, TitleClusterAnalyzer,
)


@dataclass
class ExpansionReport:
    generated_at: datetime
    days_analyzed: int
    title_clusters: list[TitleCluster] = field(default_factory=list)
    deep_targets: list[DeepTarget] = field(default_factory=list)
    adjacent_suggestions: list[AdjacentSuggestion] = field(default_factory=list)
    skill_gaps: list[SkillGap] = field(default_factory=list)

    def to_markdown(self) -> str:
        """Human-readable weekly report."""
        lines: list[str] = []
        lines.append(f"# Discovery Expansion Report")
        lines.append("")
        lines.append(
            f"_Generated {self.generated_at.isoformat(timespec='seconds')} "
            f"— analyzing past {self.days_analyzed} days._"
        )
        lines.append("")

        # --- Title clusters ---------------------------------------
        lines.append("## 1. Title clusters")
        if not self.title_clusters:
            lines.append("_No clusters of 3+ similar postings found._")
        else:
            for c in self.title_clusters:
                flag = (
                    "(already in target_role_types)" if c.already_in_target
                    else "**NEW role type — review**"
                )
                lines.append(
                    f"- **{c.modal_title}** "
                    f"({c.posting_count} postings, "
                    f"confidence {c.confidence:.2f}) {flag}"
                )
                if c.example_employers:
                    lines.append(
                        f"  - Employers: "
                        f"{', '.join(c.example_employers)}"
                    )
        lines.append("")

        # --- Deep targets ----------------------------------------
        lines.append("## 2. Go-deep employer candidates")
        if not self.deep_targets:
            lines.append("_No employers with 2+ STRONG/TOP_TIER postings._")
        else:
            for d in self.deep_targets:
                marker = " (already watched)" if d.already_watched else ""
                ats = (
                    f" — ATS: {d.ats_platform}/{d.ats_slug}"
                    if d.ats_platform else ""
                )
                lines.append(
                    f"- **{d.company_name}**{marker}: "
                    f"{d.top_tier_count} TOP_TIER + "
                    f"{d.strong_count} STRONG{ats}"
                )
        lines.append("")

        # --- Adjacent suggestions --------------------------------
        lines.append("## 3. Adjacent-employer suggestions")
        if not self.adjacent_suggestions:
            lines.append("_No adjacent companies surfaced from corpus._")
        else:
            # Group by source company for readability.
            by_source: dict[str, list[AdjacentSuggestion]] = {}
            for s in self.adjacent_suggestions:
                by_source.setdefault(s.source_company, []).append(s)
            for src, items in by_source.items():
                lines.append(f"- From **{src}** ({items[0].source_industry}):")
                for item in items:
                    lines.append(
                        f"  - {item.suggested_company} "
                        f"({item.reason}, conf {item.confidence:.2f})"
                    )
        lines.append("")

        # --- Skill gaps ------------------------------------------
        lines.append("## 4. Inventory skill gaps (from EXPLORATORY postings)")
        if not self.skill_gaps:
            lines.append("_No recurring skill gaps surfaced._")
        else:
            for g in self.skill_gaps:
                lines.append(
                    f"- **{g.skill_label}** "
                    f"({g.occurrence_count} postings) — "
                    f"`{g.skill_id}`"
                )
                if g.example_postings:
                    lines.append(
                        f"  - Examples: "
                        f"{'; '.join(g.example_postings[:3])}"
                    )
                lines.append(f"  - {g.action_prompt}")
        lines.append("")
        return "\n".join(lines)


class ExpansionOrchestrator:
    """Run all four Phase 7 expansion strategies and assemble
    a unified report.

    `profile` is a mapping with at least:
      - target_role_types: list[str]
      - target_cities:     list[str]
    `inventory_skill_ids` is the set of skill IDs already in the
    profile inventory.
    """

    def __init__(
        self,
        tracker,
        profile: dict,
        inventory_skill_ids: set[str],
    ) -> None:
        self.tracker = tracker
        self.profile = profile or {}
        self.inventory = set(inventory_skill_ids)

    def run(self, days_back: int = 14) -> ExpansionReport:
        target_roles = self.profile.get("target_role_types") or []
        target_cities = self.profile.get("target_cities") or []

        title_clusters = TitleClusterAnalyzer(self.tracker).analyze(
            days_back=days_back,
            target_role_types=list(target_roles),
        )
        deep_targets = EmployerDeepPromoter(self.tracker).identify(
            days_back=days_back,
        )
        adjacent = AdjacentEmployerFinder(
            self.tracker, target_cities=list(target_cities),
        ).suggest(days_back=days_back)
        gaps = InventoryGapSurfer(
            self.tracker, self.inventory,
        ).surface(days_back=days_back)

        return ExpansionReport(
            generated_at=datetime.now(timezone.utc),
            days_analyzed=days_back,
            title_clusters=title_clusters,
            deep_targets=deep_targets,
            adjacent_suggestions=adjacent,
            skill_gaps=gaps,
        )
