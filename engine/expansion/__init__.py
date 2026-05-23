"""Discovery Expansion Agent (Phase 7) — weekly analysis of
eval_decisions to learn what to search for.

Four strategies, run by ExpansionOrchestrator and surfaced in
the dashboard Expansion Insights tab:

  - title_clusters.TitleClusterAnalyzer    — title patterns scoring
    well that aren't in current keywords
  - employer_deep.EmployerDeepPromoter     — employers with 2+
    STRONG/TOP_TIER postings (worth deeper coverage)
  - adjacent_employers.AdjacentEmployerFinder — same-sector
    competitors of confirmed go-deep targets
  - inventory_gaps.InventoryGapFinder      — recurring requirements
    in EXPLORATORY postings not in the user's inventory
"""
