"""Step 0: per-posting gap analysis using Gemma.

Compares ONE posting's requirements against the candidate's career
inventory via a single Gemma 4 E4B LLM call. Returns a structured
report with three lists:

  COVERED — requirements clearly documented in the inventory, with
            the role and evidence phrase that supports each
  UNDOCUMENTED — requirements the candidate probably has but isn't
            explicitly documented (suggests inventory updates)
  GENUINE GAPS — requirements the candidate doesn't have (with
            mitigation strategy for the cover letter)

Plus a coverage_score and an interview_readiness label
(strong | competitive | stretch).

Why an LLM, not keyword matching: Gemma understands semantic
relationships like "budget accountability" matching "scope,
schedule, budget, and quality accountability." Keyword matching
misses these and over-flags unrelated terms.

The LLM is invoked once per call (~16s on Gemma 4 E4B). Tests mock
the LLMClient so they don't require Ollama.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from llm.client import LLMClient

logger = logging.getLogger(__name__)


# Pinned to the same DECIDE_MODEL the evaluator pipeline uses. The
# 4B-parameter variant is accurate enough for semantic mapping at
# acceptable latency on a 4 GB Pascal GPU.
GEMMA_MODEL = "gemma4:e4b"
NUM_CTX = 16384       # posting (~3.5K tok) + slim inventory (~3K tok) + JSON output (~1.5K tok)
NUM_PREDICT = 3500    # cap on output tokens
TEMPERATURE = 0.0


# --- Result dataclasses --------------------------------------------

@dataclass
class CoverageItem:
    requirement: str
    inventory_evidence: str
    inventory_role_id: str
    # "direct_match" | "transferable" | "adjacent"
    strength: str = "direct_match"


@dataclass
class UndocumentedItem:
    requirement: str
    likely_source: str        # role id from inventory.roles
    reasoning: str            # why we believe candidate has it
    suggested_update: str     # what to add to the inventory
    urgency: str = "low"      # "high" | "low"


@dataclass
class GenuineGap:
    requirement: str
    impact: str = "preferred"  # "critical" | "preferred" | "nice_to_have"
    mitigation: str = ""


@dataclass
class PostingGapReport:
    posting_id: int
    posting_title: str
    employer: str
    covered: list[CoverageItem] = field(default_factory=list)
    undocumented: list[UndocumentedItem] = field(default_factory=list)
    genuine_gaps: list[GenuineGap] = field(default_factory=list)
    coverage_score: float = 0.0
    # "strong" | "competitive" | "stretch"
    interview_readiness: str = "stretch"


# --- Inventory slimming -------------------------------------------

def _slim_inventory(inventory_extract: dict) -> dict:
    """Strip the inventory to fields the analyzer needs.

    Keeps role id/employer/title/function/skill_clusters/
    evidence_phrases for matching, plus transferable_skill_clusters
    for adjacency reasoning. Drops dates, location, outcomes, etc. —
    they don't help with requirement-vs-inventory mapping and they
    inflate the prompt past the context window.
    """
    roles = []
    for r in inventory_extract.get("roles") or []:
        roles.append({
            "id": r.get("id"),
            "employer": r.get("employer"),
            "title": r.get("title"),
            "function": r.get("function"),
            "skill_clusters": list(r.get("skill_clusters") or []),
            "evidence_phrases": list(r.get("evidence_phrases") or []),
        })
    transferable = []
    for t in inventory_extract.get("transferable_skill_clusters") or []:
        transferable.append({
            "name": t.get("name"),
            "summary": t.get("summary"),
            "evidence_role_ids": list(t.get("evidence_role_ids") or []),
        })
    return {
        "roles": roles,
        "transferable_skill_clusters": transferable,
        "trajectory": inventory_extract.get("trajectory"),
    }


# --- Prompt construction -----------------------------------------

SYSTEM_PROMPT = """You are a careful career-coach analyzer. Your job is to compare a job posting's requirements against a candidate's career inventory and classify each requirement into one of three categories.

CATEGORIES:
1. COVERED — the candidate clearly has this experience, with explicit evidence in their inventory (a skill_cluster name, an evidence_phrase, or a function description).
2. UNDOCUMENTED — the candidate probably has this experience based on adjacent roles or transferable_skill_clusters, but it is not directly documented in skill_clusters or evidence_phrases. Point to the role most likely to have it and suggest what to add.
3. GENUINE_GAPS — the candidate does not have this experience anywhere in the inventory. Suggest how to mitigate (frame as transferable, emphasize adjacent experience, mark nice-to-have, etc.).

RULES:
- Be conservative. Only classify as COVERED when the inventory has direct or near-direct evidence — quote the specific phrase or skill cluster.
- For UNDOCUMENTED, the role you point to must actually exist in the inventory.roles list (use the role's "id" field).
- For GENUINE_GAPS, suggest a concrete mitigation the candidate can use in their cover letter.
- Extract REQUIREMENTS only — skills/experience/qualifications the posting asks for. Skip generic boilerplate ("strong communication skills", "team player") unless the posting emphasizes it.
- Limit each category to the most important 8-12 items. Do not enumerate every nice-to-have phrase.
- If you cannot extract clear requirements, return empty lists.
- NEVER invent requirements that are not in the posting text.
- NEVER invent inventory evidence that is not in the inventory JSON.

OUTPUT — return ONLY a JSON object matching this schema. No prose before or after, no markdown fences, no commentary:

{
  "covered": [
    {
      "requirement": "<requirement from posting>",
      "inventory_evidence": "<verbatim phrase or skill_cluster from inventory>",
      "inventory_role_id": "<role id from inventory.roles>",
      "strength": "direct_match" | "transferable" | "adjacent"
    }
  ],
  "undocumented": [
    {
      "requirement": "<requirement from posting>",
      "likely_source": "<role id from inventory.roles>",
      "reasoning": "<why the candidate likely has this>",
      "suggested_update": "<what to add to inventory.roles[that_id]>",
      "urgency": "high" | "low"
    }
  ],
  "genuine_gaps": [
    {
      "requirement": "<requirement from posting>",
      "impact": "critical" | "preferred" | "nice_to_have",
      "mitigation": "<how to address in cover letter>"
    }
  ]
}"""


def _build_user_message(
    posting: dict,
    slim_inventory: dict,
    eval_decision: Optional[dict],
) -> str:
    parts: list[str] = []
    parts.append("POSTING:")
    parts.append(f"Employer: {posting.get('employer') or ''}")
    parts.append(f"Title: {posting.get('title') or ''}")
    parts.append(f"Location: {posting.get('location') or ''}")
    parts.append("")
    parts.append("POSTING TEXT:")
    parts.append((posting.get("posting_text") or "").strip())
    parts.append("")
    if eval_decision:
        parts.append("EVALUATOR CONTEXT (for reference only):")
        parts.append(
            f"  Tier: {eval_decision.get('tier')}, "
            f"fit_score: {eval_decision.get('fit_score')}"
        )
        if eval_decision.get("reasoning"):
            parts.append(
                f"  Reasoning: {eval_decision['reasoning']}"
            )
        parts.append("")
    parts.append("CANDIDATE INVENTORY (JSON):")
    parts.append(json.dumps(slim_inventory, indent=2, default=str))
    parts.append("")
    parts.append(
        "Output the JSON gap analysis now. Remember: ONLY the "
        "JSON object, no other text."
    )
    return "\n".join(parts)


# --- Response parsing --------------------------------------------

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _parse_response(raw: str) -> dict:
    """Same fallback chain as evaluator stages: plain json.loads,
    markdown fence, greedy {...} grab. Fail-loud."""
    if not raw:
        raise ValueError("empty response from gap analyzer")
    try:
        return json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        pass
    m = _FENCE_RE.search(raw)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except (ValueError, json.JSONDecodeError):
            pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(raw[start:end + 1])
        except (ValueError, json.JSONDecodeError):
            pass
    raise ValueError(
        f"unable to parse JSON from gap analyzer response "
        f"(first 200 chars): {raw[:200]!r}"
    )


def _to_report(data: dict, posting: dict) -> PostingGapReport:
    """Convert parsed JSON dict to PostingGapReport dataclass.
    Defensive against partial / missing fields."""
    covered: list[CoverageItem] = []
    for item in data.get("covered") or []:
        if not isinstance(item, dict):
            continue
        covered.append(CoverageItem(
            requirement=str(item.get("requirement") or ""),
            inventory_evidence=str(item.get("inventory_evidence") or ""),
            inventory_role_id=str(item.get("inventory_role_id") or ""),
            strength=str(item.get("strength") or "direct_match"),
        ))
    undocumented: list[UndocumentedItem] = []
    for item in data.get("undocumented") or []:
        if not isinstance(item, dict):
            continue
        undocumented.append(UndocumentedItem(
            requirement=str(item.get("requirement") or ""),
            likely_source=str(item.get("likely_source") or ""),
            reasoning=str(item.get("reasoning") or ""),
            suggested_update=str(item.get("suggested_update") or ""),
            urgency=str(item.get("urgency") or "low"),
        ))
    genuine_gaps: list[GenuineGap] = []
    for item in data.get("genuine_gaps") or []:
        if not isinstance(item, dict):
            continue
        genuine_gaps.append(GenuineGap(
            requirement=str(item.get("requirement") or ""),
            impact=str(item.get("impact") or "preferred"),
            mitigation=str(item.get("mitigation") or ""),
        ))

    total = len(covered) + len(undocumented) + len(genuine_gaps)
    coverage_score = (len(covered) / total) if total > 0 else 0.0
    if coverage_score >= 0.70:
        readiness = "strong"
    elif coverage_score >= 0.50:
        readiness = "competitive"
    else:
        readiness = "stretch"

    employer = (
        posting.get("employer")
        or (posting.get("company") if isinstance(posting.get("company"), str) else None)
        or ""
    )

    return PostingGapReport(
        posting_id=int(posting.get("id") or 0),
        posting_title=str(posting.get("title") or ""),
        employer=str(employer),
        covered=covered,
        undocumented=undocumented,
        genuine_gaps=genuine_gaps,
        coverage_score=coverage_score,
        interview_readiness=readiness,
    )


# --- Main analyzer class -----------------------------------------

class PostingGapAnalyzer:
    """Run gap analysis for one posting via a single Gemma call.

    The LLM client is injectable so tests can mock it. Defaults to
    a fresh LLMClient pointed at the local Ollama service.
    """

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        model: str = GEMMA_MODEL,
    ):
        self.llm_client = llm_client or LLMClient()
        self.model = model

    def analyze(
        self,
        posting: dict,
        inventory_extract: dict,
        eval_decision: Optional[dict] = None,
    ) -> PostingGapReport:
        """Return a PostingGapReport for this posting.

        Raises ValueError if Gemma's response can't be parsed as
        JSON — fail-loud, matching the evaluator pipeline pattern.
        """
        slim = _slim_inventory(inventory_extract)
        user_message = _build_user_message(posting, slim, eval_decision)

        data = self.llm_client.generate_chat(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_message,
            temperature=TEMPERATURE,
            num_predict=NUM_PREDICT,
            num_ctx=NUM_CTX,
            model=self.model,
            keep_alive=-1,
            think=False,
        )
        raw = data.get("response") or ""
        parsed = _parse_response(raw)
        return _to_report(parsed, posting)
