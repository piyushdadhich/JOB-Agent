"""LocalFormFiller -- zero-cost per-field application form filler.

Local Gemma 4 E4B via Ollama HTTP API at localhost:11434. One call
per field (~700ms each on a GTX 1050 Ti). Use when you want zero
cloud cost and don't care about wall-clock time.

Pairs with QAMatcher as a Tier-0 fast path: common questions
(work auth, demographics, salary expectation, file uploads) match
deterministic patterns and skip the LLM entirely.

Field detection is locator-based, not LLM-based. The CLI is
responsible for pausing for human review -- this class never
clicks submit.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field as dc_field
from datetime import date
from typing import Optional

import requests

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "gemma4:e4b"
DEFAULT_TIMEOUT_SEC = 30
DEFAULT_NUM_PREDICT = 64    # answers are short; cap output


@dataclass
class DetectedField:
    label: str
    field_type: str           # text | email | tel | number | url |
                              # textarea | dropdown | radio |
                              # checkbox | file
    options: list[str] = dc_field(default_factory=list)
    required: bool = True
    group_name: Optional[str] = None  # radio groups
    locator: object = None    # Playwright Locator (None for radio)


def format_profile_for_prompt(profile) -> str:
    """Compact context string from an ApplicantProfile.

    Only emits fields with non-empty values. Booleans and numerics
    use explicit framing so the LLM can quote them verbatim.
    """
    rows: list[str] = []
    string_attrs = (
        "first_name", "last_name", "email", "phone",
        "city", "province", "country", "postal_code",
        "linkedin_url", "portfolio_url",
        "work_authorization", "salary_expectation",
        "salary_currency", "start_date",
    )
    for attr in string_attrs:
        v = getattr(profile, attr, None)
        if v not in (None, ""):
            rows.append(f"{attr}: {v}")
    if getattr(profile, "willing_to_relocate", False):
        rows.append("willing_to_relocate: yes")
    # requires_sponsorship is emitted both ways -- the answer is
    # almost always "no" and that's a fact the LLM should see, not
    # something we want it to guess from name+location alone.
    if getattr(profile, "requires_sponsorship", False):
        rows.append("requires_sponsorship: yes")
    else:
        rows.append("requires_sponsorship: no")
    for attr in ("salary_min", "salary_max", "total_years_experience"):
        v = getattr(profile, attr, 0)
        if v:
            rows.append(f"{attr}: {v}")
    edu = getattr(profile, "education", None) or []
    if edu:
        first = edu[0]
        rows.append(f"highest_degree: {first.degree}")
        if getattr(first, "school", None):
            rows.append(f"university: {first.school}")
    certs = getattr(profile, "certifications", None) or []
    active_names = [c.name for c in certs if getattr(c, "active", True)]
    if active_names:
        rows.append(f"certifications: {', '.join(active_names)}")
    return ". ".join(rows)


class LocalFormFiller:
    def __init__(
        self,
        profile,
        qa_matcher=None,
        model: str = DEFAULT_MODEL,
        ollama_url: str = OLLAMA_URL,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    ):
        self.profile = profile
        self.qa_matcher = qa_matcher
        self.model = model
        self.ollama_url = ollama_url
        self.timeout_sec = timeout_sec
        self._profile_context = format_profile_for_prompt(profile)

    # ---- LLM ------------------------------------------------------

    def _build_prompt(
        self,
        label: str,
        field_type: str,
        options: list[str],
        required: bool,
    ) -> str:
        today = date.today().isoformat()
        options_str = (
            f"Options: {', '.join(options)}"
            if options else "Free text"
        )
        required_str = (
            "Required." if required
            else "Optional (leave blank if not applicable)."
        )
        return (
            f"Today is {today}. {required_str} "
            f"Respond with ONLY the value to enter, nothing else. "
            f"No explanation. No quotes. "
            f"Field: '{label}'. Type: {field_type}. {options_str}. "
            f"Context: {self._profile_context} /no_think"
        )

    def _ollama_call(self, prompt: str) -> str:
        resp = requests.post(
            self.ollama_url,
            json={
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.0,
                    "num_predict": DEFAULT_NUM_PREDICT,
                },
            },
            timeout=self.timeout_sec,
        )
        resp.raise_for_status()
        return (resp.json().get("response") or "").strip()

    @staticmethod
    def _clean_llm_value(raw: str) -> str:
        """Strip <think> blocks, surrounding quotes, take first line."""
        s = raw.strip()
        # Gemma /no_think can still emit empty <think></think> blocks
        # in some builds. Strip them defensively.
        while "<think>" in s and "</think>" in s:
            start = s.index("<think>")
            end = s.index("</think>", start) + len("</think>")
            s = (s[:start] + s[end:]).strip()
        if (s.startswith('"') and s.endswith('"')) or \
           (s.startswith("'") and s.endswith("'")):
            s = s[1:-1].strip()
        if s:
            s = s.splitlines()[0].strip()
        return s

    def fill_field(
        self,
        label: str,
        field_type: str = "text",
        options: Optional[list[str]] = None,
        required: bool = True,
    ) -> tuple[str, str]:
        """Decide the value for one field.

        Returns (value, source) where source is "qa_matcher" or "llm".
        """
        if self.qa_matcher is not None:
            match = self.qa_matcher.match(label)
            if match is not None and match.answer is not None:
                return match.answer, "qa_matcher"
        prompt = self._build_prompt(
            label=label,
            field_type=field_type,
            options=options or [],
            required=required,
        )
        raw = self._ollama_call(prompt)
        return self._clean_llm_value(raw), "llm"

    # ---- Playwright: detect ---------------------------------------

    def detect_fields(self, page) -> list[DetectedField]:
        fields: list[DetectedField] = []

        text_selector = (
            "input[type='text'], input[type='email'], "
            "input[type='tel'], input[type='number'], "
            "input[type='url'], input:not([type])"
        )
        for inp in page.locator(text_selector).all():
            if not inp.is_visible():
                continue
            label = self._get_label(page, inp)
            if not label:
                continue
            t = (inp.get_attribute("type") or "text").lower()
            fields.append(DetectedField(
                label=label, field_type=t,
                required=inp.get_attribute("required") is not None,
                locator=inp,
            ))

        for ta in page.locator("textarea").all():
            if not ta.is_visible():
                continue
            label = self._get_label(page, ta)
            if not label:
                continue
            fields.append(DetectedField(
                label=label, field_type="textarea",
                required=ta.get_attribute("required") is not None,
                locator=ta,
            ))

        for sel in page.locator("select").all():
            if not sel.is_visible():
                continue
            label = self._get_label(page, sel)
            if not label:
                continue
            opts: list[str] = []
            for opt in sel.locator("option").all():
                t = opt.inner_text().strip()
                if t:
                    opts.append(t)
            fields.append(DetectedField(
                label=label, field_type="dropdown",
                options=opts,
                required=sel.get_attribute("required") is not None,
                locator=sel,
            ))

        seen_radio: set[str] = set()
        for radio in page.locator("input[type='radio']").all():
            if not radio.is_visible():
                continue
            name = radio.get_attribute("name")
            if not name or name in seen_radio:
                continue
            seen_radio.add(name)
            opts: list[str] = []
            for r in page.locator(
                f"input[type='radio'][name='{name}']"
            ).all():
                lbl = self._get_label(page, r)
                if lbl:
                    opts.append(lbl)
            label_text: Optional[str] = None
            fieldset = radio.locator("xpath=ancestor::fieldset[1]")
            if fieldset.count() > 0:
                legend = fieldset.locator("legend")
                if legend.count() > 0:
                    t = legend.first.inner_text().strip()
                    if t:
                        label_text = t
            label_text = label_text or (opts[0] if opts else name)
            fields.append(DetectedField(
                label=label_text, field_type="radio",
                options=opts,
                required=radio.get_attribute("required") is not None,
                group_name=name,
            ))

        for cb in page.locator("input[type='checkbox']").all():
            if not cb.is_visible():
                continue
            label = self._get_label(page, cb)
            if not label:
                continue
            fields.append(DetectedField(
                label=label, field_type="checkbox",
                required=cb.get_attribute("required") is not None,
                locator=cb,
            ))

        for f in page.locator("input[type='file']").all():
            if not f.is_visible():
                continue
            label = self._get_label(page, f) or "Resume"
            fields.append(DetectedField(
                label=label, field_type="file",
                required=f.get_attribute("required") is not None,
                locator=f,
            ))

        return fields

    @staticmethod
    def _get_label(page, element) -> Optional[str]:
        aria = element.get_attribute("aria-label")
        if aria and aria.strip():
            return aria.strip()
        elem_id = element.get_attribute("id")
        if elem_id:
            lab = page.locator(f"label[for='{elem_id}']")
            if lab.count() > 0:
                txt = lab.first.inner_text().strip()
                if txt:
                    return txt
        placeholder = element.get_attribute("placeholder")
        if placeholder and placeholder.strip():
            return placeholder.strip()
        parent_label = element.locator("xpath=ancestor::label[1]")
        if parent_label.count() > 0:
            txt = parent_label.first.inner_text().strip()
            if txt:
                return txt
        name = element.get_attribute("name")
        if name:
            return name.replace("_", " ").replace("-", " ").title()
        return None

    # ---- Playwright: fill ----------------------------------------

    def fill_one(
        self, page, df: DetectedField, value: str,
        resume_path: Optional[str] = None,
    ) -> None:
        t = df.field_type
        loc = df.locator
        if t in ("text", "email", "tel", "number", "url", "textarea"):
            loc.fill(value)
        elif t == "dropdown":
            try:
                loc.select_option(label=value)
            except Exception:
                match = next(
                    (o for o in df.options if value.lower() in o.lower()),
                    None,
                )
                if match is None:
                    raise
                loc.select_option(label=match)
        elif t == "radio":
            if not df.group_name:
                return
            target = next(
                (o for o in df.options if value.lower() in o.lower()),
                None,
            ) or value
            radio_loc = page.locator(
                f"input[type='radio'][name='{df.group_name}']"
            )
            for r in radio_loc.all():
                rl = self._get_label(page, r)
                if rl and (target.lower() in rl.lower() or
                           rl.lower() in target.lower()):
                    r.check()
                    return
            page.locator(
                f"input[type='radio'][name='{df.group_name}']"
                f"[value='{value}']"
            ).check()
        elif t == "checkbox":
            if value.lower() in ("yes", "true", "check", "1", "on"):
                loc.check()
            else:
                loc.uncheck()
        elif t == "file":
            if resume_path:
                loc.set_input_files(resume_path)
            else:
                logger.warning(
                    "file field %r detected but no resume_path",
                    df.label,
                )

    # ---- Top-level ------------------------------------------------

    def run(
        self, page, resume_path: Optional[str] = None,
    ) -> dict:
        """Detect + fill every visible field. Returns a summary dict.

        Does NOT click submit. Caller pauses for human review.
        """
        detected = self.detect_fields(page)
        filled: list[dict] = []
        skipped: list[dict] = []
        for df in detected:
            try:
                value, source = self.fill_field(
                    label=df.label,
                    field_type=df.field_type,
                    options=df.options,
                    required=df.required,
                )
                if df.field_type == "file":
                    self.fill_one(
                        page, df, value, resume_path=resume_path,
                    )
                    actual = resume_path or "(no resume path)"
                else:
                    self.fill_one(page, df, value)
                    actual = value
                filled.append({
                    "label": df.label, "type": df.field_type,
                    "value": actual, "source": source,
                })
            except Exception as e:
                logger.warning(
                    "LocalFormFiller failed on field %r: %s",
                    df.label, e,
                )
                skipped.append({
                    "label": df.label, "type": df.field_type,
                    "error": str(e),
                })
        return {
            "detected": len(detected),
            "filled": filled,
            "skipped": skipped,
        }
