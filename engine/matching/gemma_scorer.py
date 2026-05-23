"""Gemma-based contextual scorer for high-overlap postings.

Takes the pre-computed intersection from the deterministic
scorer, looks up human-readable labels from skill_labels table,
sends a structured prompt to Gemma 3 4B via Ollama HTTP API.

Validates Gemma's response against the known intersection to
catch hallucination. Persists Gemma fields to match_scores.

This module has NO ML dependencies. It calls Ollama over HTTP
at localhost:11434. Runs in venv\\.
"""
from __future__ import annotations

import json

import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "gemma4:e4b"
TEMPERATURE = 0
NUM_PREDICT = 1024
REQUEST_TIMEOUT_SEC = 120


def build_prompt(
    posting_title: str,
    posting_employer: str,
    posting_labels: list[str],
    inventory_labels: list[str],
    overlap_labels: list[str],
) -> str:
    """Build the structured fit-evaluation prompt.

    Notes:
      - Persona is fixed to the user's career profile. If we ever
        need profile-agnostic scoring, this becomes a parameter.
      - DIRECT OVERLAPS is named explicitly so Gemma's
        'top_matches' must come from this list -- the prompt
        instructs it, and validate_gemma_response checks it.
      - Output is JSON-only. Markdown fences are stripped in
        call_gemma; if Gemma still returns prose, json.loads
        raises ValueError and the caller logs it.
    """
    return f"""You are evaluating job-posting fit for a senior
delivery/operations professional with 10+ years of experience
in financial services, payments, SAP implementation, land
services, agile delivery, and project management.

POSTING: {posting_employer} -- {posting_title}

POSTING SKILLS (extracted from the job posting):
{json.dumps(posting_labels, indent=2)}

CANDIDATE SKILLS (from career inventory):
{json.dumps(inventory_labels, indent=2)}

DIRECT OVERLAPS (skills in both lists -- these are confirmed):
{json.dumps(overlap_labels, indent=2)}

TASK:
1. Score the overall fit from 1-10.
2. List the top 5 strongest matching skills with one-line
   reasoning each. Use only skills from DIRECT OVERLAPS.
3. List up to 3 transferable skills -- posting skills NOT in
   DIRECT OVERLAPS but that the candidate could credibly
   perform based on adjacent candidate skills. For each,
   name the candidate skill that bridges to it.
4. List the top 3 critical gaps -- posting skills the candidate
   lacks and that cannot be bridged.
5. One-sentence summary of fit.

Output ONLY valid JSON (no markdown, no backticks):
{{"score": <int 1-10>,
  "top_matches": [{{"posting_skill": "...",
                    "reason": "..."}}],
  "transferable": [{{"posting_skill": "...",
                     "bridged_from": "...",
                     "reason": "..."}}],
  "critical_gaps": ["skill1", "skill2", "skill3"],
  "summary": "..."}}"""


def call_gemma(prompt: str) -> tuple[dict, str]:
    """Call Ollama Gemma and parse JSON response.

    Returns (parsed_dict, raw_response_text).
    Raises ValueError on JSON parse failure (caller logs and
    skips the posting).
    Raises requests.HTTPError on non-2xx HTTP.
    Raises requests.Timeout on >120s.
    """
    resp = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": TEMPERATURE,
                "num_predict": NUM_PREDICT,
            },
        },
        timeout=REQUEST_TIMEOUT_SEC,
    )
    resp.raise_for_status()
    raw = resp.json().get("response", "")

    # Strip markdown fences if Gemma wraps in ```json ... ```
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        # drop opening fence (```json or ```)
        cleaned = cleaned.split("\n", 1)[-1]
    if cleaned.endswith("```"):
        cleaned = cleaned.rsplit("```", 1)[0]
    cleaned = cleaned.strip()

    parsed = json.loads(cleaned)
    return parsed, raw


def validate_gemma_response(
    parsed: dict,
    overlap_labels: set[str],
) -> int:
    """Count hallucination flags.

    A hallucination flag is when Gemma claims a skill is a
    'top match' but it's not in the pre-computed overlap.
    Returns the count (0 = clean response).
    """
    flags = 0
    for m in parsed.get("top_matches", []):
        if not isinstance(m, dict):
            flags += 1
            continue
        skill = m.get("posting_skill", "")
        if skill and skill not in overlap_labels:
            flags += 1
    return flags
