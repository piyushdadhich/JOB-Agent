"""Spec 8 TASK 1 — resume prompt generator (LLM-agnostic).

Thin facade over the existing :class:`ResumePromptBuilder` from
``prompt_builder.py``. Where that module takes posting + eval +
gap inputs and produces a Claude-Max-style instruction bundle,
this one packages the same prompt with a different framing: a
self-contained string that the resume *router* (TASK 2) can hand
verbatim to Claude, OpenAI, Gemini, Ollama, or the user's clipboard.

The two key adaptations:

  1. Strip out any "paste this into Claude.ai Max" or other client-
     specific framing — the prompt is LLM-agnostic.
  2. Surface a header block summarising matched / missed skills and
     a bridge explanation so even a generic LLM has the same
     scoring context the dedicated Claude-Max bundle gets.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from engine.resume.prompt_builder import ResumePromptBuilder


@dataclass(frozen=True)
class ResumePromptInputs:
    posting: dict                       # opportunity row + posting_text
    eval_decision: Optional[dict] = None
    scorer_output: Optional[dict] = None  # {matched, missed, bridge_note}
    inventory_text: str = ""
    profile_config: Optional[dict] = None


def generate(inputs: ResumePromptInputs) -> str:
    """Return a self-contained resume-tailoring prompt string.

    The output is a single block suitable for clipboard paste OR
    direct submission to any chat-completions endpoint. No system
    prompt needed; the prompt embeds its own framing.
    """
    builder = ResumePromptBuilder()
    body = builder.build_prompt(
        posting=inputs.posting,
        eval_decision=inputs.eval_decision,
        inventory_text=inputs.inventory_text,
        profile_config=inputs.profile_config,
    )
    header = _scorer_header(inputs.scorer_output)
    if header:
        return f"{header}\n\n{body}"
    return body


def _scorer_header(scorer_output: Optional[dict]) -> str:
    if not scorer_output:
        return ""
    matched = scorer_output.get("matched") or []
    missed = scorer_output.get("missed") or []
    bridge = scorer_output.get("bridge_note") or ""
    lines = ["SCORER CONTEXT (use these signals when tailoring):"]
    if matched:
        lines.append(
            "Matched skills (lean into these): "
            + ", ".join(sorted(matched)[:20])
            + (f" + {len(matched) - 20} more" if len(matched) > 20 else "")
        )
    if missed:
        lines.append(
            "Missing from inventory (use bridge framing if relevant): "
            + ", ".join(sorted(missed)[:20])
            + (f" + {len(missed) - 20} more" if len(missed) > 20 else "")
        )
    if bridge:
        lines.append(f"Bridge framing hint: {bridge}")
    return "\n".join(lines)


def estimate_token_count(prompt: str) -> int:
    """Rough character-based token estimate (~4 chars/token).

    Used by the cost estimator to forecast spend per resume. Don't
    rely on this for hard budgets — providers' tokenisers differ.
    """
    return max(1, len(prompt) // 4)
