"""Universal form filler — Smart Agent upgrade Phase 3.

Three-stage pipeline per form page:

  1. Extract fields  (SmartScraper / Ollama / Gemma 3 4B — free, local)
  2. Decide fills    (GemmaCloudClient / Gemma 4 31B — 1 cloud call/page)
  3. Execute fills   (Playwright — deterministic, no LLM)

This is an ADDITIVE component. The 5 ATS-specific handlers under
engine/applicant/handlers/ remain in the codebase and remain wired
into apply.py. SmartFormFiller is the universal-coverage layer; the
caller decides (via plan validation) whether to use its plan or fall
back to a known handler.

QAMatcher (Tier-0) runs FIRST for each field. When it has an answer,
that field is filled with zero LLM cost. Only fields the matcher can't
handle are sent to the cloud LLM in step 2.

Plan validation: each action's selector is verified against the live
page (locator must resolve to >= 1 element AND the element type must
match the action). The caller receives a confidence score it can use
to decide between executing the plan and dispatching to a handler.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from engine.discovery.smart_scraper import SmartScraper

logger = logging.getLogger(__name__)


# --- Prompts -----------------------------------------------------------

FORM_EXTRACT_PROMPT = """Extract every visible form field on this page.
For each field, return a JSON object with these exact keys:
  - index: sequential number starting at 1
  - label: the field's label text (string)
  - type: one of "text", "email", "tel", "textarea", "select",
    "radio", "checkbox", "file", "date", "number"
  - placeholder: placeholder text if any (string or null)
  - options: for select/radio, list the available options (array of strings)
  - required: true if the field is marked required, false otherwise
  - current_value: if pre-filled, the current value (string or null)
  - selector: a CSS selector that uniquely identifies this input element

Return a JSON array. If no form fields found, return [].
Do NOT include submit buttons, hidden CSRF tokens, or navigation."""


FILL_DECISION_SYSTEM = (
    "You fill job application forms accurately. Use only facts from "
    "the provided profile and inventory. Never fabricate. Return "
    "ONLY valid JSON, no markdown, no preamble."
)


FILL_DECISION_PROMPT_TEMPLATE = """You are filling a job application form.

CANDIDATE PROFILE:
{profile}

CANDIDATE CAREER INVENTORY (select relevant accomplishments):
{inventory}

JOB POSTING:
{posting_title} at {posting_employer}
{posting_description}

FORM FIELDS:
{fields_json}

For each field, decide what to fill. Return a JSON array where each element has:
  - field: the field index number
  - action: one of "fill", "select", "upload", "check", "skip"
  - value: text to type, option to select, or null for upload/skip
  - file: for upload actions only — "resume" or "cover_letter"

RULES:
1. Use ONLY facts from the profile and inventory. Do NOT fabricate.
2. For "Why this role?" or behavioural questions: write 2-4 sentences
   referencing specific accomplishments from the inventory.
3. For years of experience: count from the candidate's earliest role.
4. For work authorization: use exactly the value from the profile.
5. For demographic questions (gender, ethnicity, veteran, disability):
   always "Prefer not to say" or "Decline to self-identify".
6. For file uploads: action="upload", file="resume" or file="cover_letter".
7. Skip fields you cannot determine from profile/inventory.

Return ONLY the JSON array."""


# --- Result types -----------------------------------------------------

@dataclass(frozen=True)
class FillAction:
    field_index: int
    action: str            # fill | select | upload | check | skip
    value: Optional[str] = None
    file: Optional[str] = None  # resume | cover_letter
    source: str = "llm"    # llm | qa_matcher | rule

    @classmethod
    def from_dict(cls, raw: dict, *, default_source: str = "llm") -> "FillAction":
        return cls(
            field_index=int(raw.get("field") or raw.get("field_index") or 0),
            action=str(raw.get("action") or "skip"),
            value=raw.get("value"),
            file=raw.get("file"),
            source=str(raw.get("source") or default_source),
        )

    def to_dict(self) -> dict:
        out = {"field": self.field_index, "action": self.action}
        if self.value is not None:
            out["value"] = self.value
        if self.file is not None:
            out["file"] = self.file
        out["source"] = self.source
        return out


@dataclass(frozen=True)
class PlanValidation:
    """Confidence signals for a fill plan against a live page."""
    fraction_valid: float       # 0.0–1.0 across non-skip actions
    valid_count: int
    invalid_count: int
    skip_count: int
    required_covered: float     # 0.0–1.0 fraction of required fields with non-skip action
    issues: list[str] = field(default_factory=list)

    @property
    def is_trustworthy(self) -> bool:
        # Threshold tuned to err on the side of falling back when in
        # doubt: most actions must hit a real element AND most required
        # fields must be addressed. Caller can override by inspecting
        # the individual numbers.
        return self.fraction_valid >= 0.7 and self.required_covered >= 0.7


@dataclass
class FillExecutionResult:
    fields_filled: list[str] = field(default_factory=list)
    questions_answered: dict = field(default_factory=dict)
    fields_skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# --- The filler --------------------------------------------------------

class SmartFormFiller:
    """Universal form filler — runs the 3-stage pipeline per page.

    Construction is cheap; the heavy components (SmartScraper, cloud
    client, matcher) are passed in. apply.py and apply_service.py both
    instantiate one per session.
    """

    def __init__(
        self,
        scraper: SmartScraper,
        cloud_client,                    # GemmaCloudClient (typed Any to avoid import in tests)
        profile,                         # ApplicantProfile
        inventory_summary: str,
        qa_matcher=None,                 # optional Tier-0 fast path
    ):
        self.scraper = scraper
        self.cloud = cloud_client
        self.profile = profile
        self.inventory = inventory_summary
        self.qa_matcher = qa_matcher

    # ---- Step 1: extract -------------------------------------------

    async def extract_fields(self, page) -> list[dict]:
        """Pull a snapshot of the page HTML, ask the LLM to list fields."""
        try:
            html = await page.content()
        except Exception as e:
            logger.warning("smart_filler: page.content() failed: %s", e)
            return []

        # SmartScraper accepts either a URL or raw HTML as `source`.
        # We pass HTML so we don't trigger a second fetch + render.
        result = self.scraper._run(FORM_EXTRACT_PROMPT, html)
        if result is None:
            return []
        if isinstance(result, dict):
            for key in ("fields", "form_fields", "content", "items"):
                value = result.get(key)
                if isinstance(value, list):
                    result = value
                    break
        if not isinstance(result, list):
            return []
        # Normalize each entry to ensure required keys are present.
        out = []
        for i, item in enumerate(result):
            if not isinstance(item, dict):
                continue
            normalized = dict(item)
            normalized.setdefault("index", i + 1)
            normalized.setdefault("label", "")
            normalized.setdefault("type", "text")
            normalized.setdefault("required", False)
            normalized.setdefault("options", [])
            out.append(normalized)
        return out

    # ---- Step 2a: Tier-0 matcher fast path -------------------------

    def _qa_matcher_actions(
        self, fields: list[dict], resume_strategy_label: str = "resume",
        cl_strategy_label: str = "cover_letter",
    ) -> dict[int, FillAction]:
        """For each field with a label QAMatcher recognizes, return an
        action keyed by field index. Skips file inputs (handled by
        upload mapping in the LLM step) and unsupported action types."""
        if self.qa_matcher is None:
            return {}
        out: dict[int, FillAction] = {}
        for f in fields:
            label = (f.get("label") or "").strip()
            if not label:
                continue
            match = self.qa_matcher.match(label)
            if match is None:
                continue
            if match.answer is not None:
                out[int(f["index"])] = FillAction(
                    field_index=int(f["index"]),
                    action="select" if (f.get("type") == "select") else "fill",
                    value=match.answer,
                    source="qa_matcher",
                )
            elif match.strategy == "upload_resume" and f.get("type") == "file":
                out[int(f["index"])] = FillAction(
                    field_index=int(f["index"]),
                    action="upload",
                    file=resume_strategy_label,
                    source="qa_matcher",
                )
            elif match.strategy == "upload_cover_letter" and f.get("type") == "file":
                out[int(f["index"])] = FillAction(
                    field_index=int(f["index"]),
                    action="upload",
                    file=cl_strategy_label,
                    source="qa_matcher",
                )
        return out

    # ---- Step 2b: LLM decision ------------------------------------

    def _format_profile(self) -> str:
        """Compact, readable profile snippet for the prompt. Trimmed to
        keep prompt budget reasonable — Gemma 4 31B free tier has plenty
        of context but we still want to avoid wasted tokens."""
        p = self.profile
        rows = []
        for attr in (
            "first_name", "last_name", "email", "phone",
            "city", "province", "country", "postal_code",
            "linkedin_url", "work_authorization",
            "requires_sponsorship", "willing_to_relocate",
            "salary_expectation", "start_date",
        ):
            value = getattr(p, attr, None)
            if value not in (None, ""):
                rows.append(f"  {attr}: {value}")
        return "\n".join(rows) or "  (profile fields unavailable)"

    def decide_fills(
        self, fields: list[dict], posting: dict,
    ) -> list[FillAction]:
        """Returns one FillAction per field. Tier-0 matcher first, then
        a single cloud-LLM call for the remainder."""
        if not fields:
            return []

        tier0 = self._qa_matcher_actions(fields)
        # Fields the matcher already handled don't need the LLM.
        remaining = [f for f in fields if int(f["index"]) not in tier0]

        llm_actions: list[FillAction] = []
        if remaining:
            prompt = FILL_DECISION_PROMPT_TEMPLATE.format(
                profile=self._format_profile(),
                inventory=(self.inventory or "")[:4000],
                posting_title=posting.get("title", "") or posting.get(
                    "opportunity_title", ""
                ),
                posting_employer=posting.get("employer", ""),
                posting_description=(posting.get("posting_text") or "")[:2000],
                fields_json=json.dumps(remaining, indent=2),
            )
            try:
                response = self.cloud.generate(
                    prompt=prompt,
                    system=FILL_DECISION_SYSTEM,
                    temperature=0.2,
                )
            except Exception as e:
                logger.warning("smart_filler: cloud generate failed: %s", e)
                response = "[]"
            llm_actions = _parse_actions_json(response)

        # Merge: matcher actions take precedence, LLM fills the rest.
        merged: dict[int, FillAction] = dict(tier0)
        for a in llm_actions:
            if a.field_index in merged:
                continue
            merged[a.field_index] = a

        # Default any field with no decision to "skip" so executors can
        # report it cleanly.
        for f in fields:
            idx = int(f["index"])
            if idx not in merged:
                merged[idx] = FillAction(
                    field_index=idx, action="skip", source="default",
                )

        return [merged[int(f["index"])] for f in fields]

    # ---- Plan validation ------------------------------------------

    async def validate_plan(
        self, page, fields: list[dict], actions: list[FillAction],
    ) -> PlanValidation:
        """Verify that each non-skip action's selector resolves on the
        page and that the action type is compatible with the field type.

        We catch exceptions per field — Playwright can throw on malformed
        selectors and we want to count those as invalid, not crash.
        """
        field_map = {int(f["index"]): f for f in fields}
        valid = 0
        invalid = 0
        skip = 0
        issues: list[str] = []
        required_indices = {
            int(f["index"]) for f in fields if f.get("required")
        }
        required_addressed: set[int] = set()

        for action in actions:
            f = field_map.get(action.field_index)
            if f is None:
                invalid += 1
                issues.append(
                    f"action references unknown field {action.field_index}"
                )
                continue

            if action.action == "skip":
                skip += 1
                continue

            selector = f.get("selector")
            if not selector or not isinstance(selector, str):
                invalid += 1
                issues.append(f"missing selector for {f.get('label')!r}")
                continue

            if not _action_matches_field_type(action.action, f.get("type", "")):
                invalid += 1
                issues.append(
                    f"action {action.action} incompatible with field "
                    f"type {f.get('type')!r} ({f.get('label')!r})"
                )
                continue

            try:
                count = await page.locator(selector).count()
            except Exception as e:
                invalid += 1
                issues.append(
                    f"selector {selector!r} threw: {e}"
                )
                continue

            if count == 0:
                invalid += 1
                issues.append(
                    f"selector {selector!r} matched nothing "
                    f"({f.get('label')!r})"
                )
                continue

            valid += 1
            if action.field_index in required_indices:
                required_addressed.add(action.field_index)

        non_skip_total = valid + invalid
        fraction_valid = (valid / non_skip_total) if non_skip_total > 0 else 1.0
        required_covered = (
            len(required_addressed) / len(required_indices)
            if required_indices else 1.0
        )
        return PlanValidation(
            fraction_valid=fraction_valid,
            valid_count=valid,
            invalid_count=invalid,
            skip_count=skip,
            required_covered=required_covered,
            issues=issues,
        )

    # ---- Step 3: execute ------------------------------------------

    async def execute_fills(
        self,
        page,
        actions: list[FillAction],
        fields: list[dict],
        resume_path: Path,
        cover_letter_path: Path,
    ) -> FillExecutionResult:
        """Run each action against the page. Each action is independently
        wrapped in try/except so one bad selector doesn't abort the whole
        page's fill."""
        result = FillExecutionResult()
        field_map = {int(f["index"]): f for f in fields}

        for action in actions:
            f = field_map.get(action.field_index)
            label = (f or {}).get("label", f"field_{action.field_index}")

            if action.action == "skip" or f is None:
                result.fields_skipped.append(label)
                continue

            selector = f.get("selector")
            if not selector:
                result.fields_skipped.append(label)
                continue

            try:
                loc = page.locator(selector).first
                if await loc.count() == 0:
                    result.fields_skipped.append(label)
                    continue

                if action.action == "fill":
                    await loc.fill(action.value or "")
                    result.fields_filled.append(label)
                    if (f.get("type") or "").lower() == "textarea":
                        result.questions_answered[label] = action.value or ""
                elif action.action == "select":
                    val = action.value or ""
                    try:
                        await loc.select_option(label=val)
                    except Exception:
                        await loc.select_option(value=val)
                    result.fields_filled.append(label)
                    result.questions_answered[label] = val
                elif action.action == "upload":
                    file_kind = (action.file or "resume").lower()
                    target = (
                        cover_letter_path if file_kind == "cover_letter"
                        else resume_path
                    )
                    await loc.set_input_files(str(target))
                    result.fields_filled.append(label)
                elif action.action == "check":
                    await loc.check()
                    result.fields_filled.append(label)
                else:
                    result.fields_skipped.append(label)
            except Exception as e:
                logger.warning(
                    "smart_filler: fill failed for %r: %s", label, e,
                )
                result.errors.append(f"{label}: {e}")
                result.fields_skipped.append(label)

        return result

    # ---- Multi-step wizards ---------------------------------------

    async def fill_multi_step(
        self,
        page,
        posting: dict,
        resume_path: Path,
        cover_letter_path: Path,
        max_pages: int = 10,
        next_button_selector: Optional[str] = None,
    ) -> FillExecutionResult:
        """Repeat the 3-stage pipeline once per wizard page until no more
        fields are extracted or the Next button doesn't appear."""
        combined = FillExecutionResult()
        next_sel = next_button_selector or _DEFAULT_NEXT_BUTTON_SELECTORS

        for _ in range(max_pages):
            fields = await self.extract_fields(page)
            if not fields:
                break

            actions = self.decide_fills(fields, posting)
            page_result = await self.execute_fills(
                page, actions, fields, resume_path, cover_letter_path,
            )
            combined.fields_filled.extend(page_result.fields_filled)
            combined.fields_skipped.extend(page_result.fields_skipped)
            combined.errors.extend(page_result.errors)
            combined.questions_answered.update(page_result.questions_answered)

            try:
                next_btn = page.locator(next_sel).first
                if await next_btn.count() == 0:
                    break
                await next_btn.click()
                await page.wait_for_load_state(
                    "networkidle", timeout=20000,
                )
            except Exception as e:
                logger.info(
                    "smart_filler: no next page (%s); ending wizard", e,
                )
                break

        return combined


# --- Helpers -----------------------------------------------------------

# Standard "advance to next wizard page" selectors. Used as a single
# comma-separated CSS list because Playwright accepts that.
_DEFAULT_NEXT_BUTTON_SELECTORS = (
    "button:has-text('Next'),"
    "button:has-text('Continue'),"
    "button:has-text('Save and Continue'),"
    "[data-automation-id='pageFooterNextButton']"
)


_ACTION_FIELD_COMPAT: dict[str, set[str]] = {
    "fill": {
        "text", "email", "tel", "textarea",
        "number", "date", "search", "url", "password",
    },
    "select": {"select"},
    "check": {"checkbox", "radio"},
    "upload": {"file"},
}


def _action_matches_field_type(action: str, field_type: str) -> bool:
    """True when the action is appropriate for the field's HTML type."""
    if action == "skip":
        return True
    allowed = _ACTION_FIELD_COMPAT.get(action)
    if allowed is None:
        return False
    return (field_type or "").lower() in allowed


def _parse_actions_json(response: str) -> list[FillAction]:
    """Parse the LLM's JSON-array response into FillActions. Handles a
    few common formats (bare array, code-fenced array, single object
    wrapped in an envelope)."""
    text = (response or "").strip()
    if not text:
        return []
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        parsed: Any = json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning("smart_filler: action response not JSON: %s", e)
        return []
    if isinstance(parsed, dict):
        for key in ("actions", "fills", "items"):
            value = parsed.get(key)
            if isinstance(value, list):
                parsed = value
                break
    if not isinstance(parsed, list):
        return []
    out = []
    for raw in parsed:
        if isinstance(raw, dict):
            try:
                out.append(FillAction.from_dict(raw))
            except (TypeError, ValueError):
                continue
    return out
