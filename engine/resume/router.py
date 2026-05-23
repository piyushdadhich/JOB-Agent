"""Spec 8 TASK 2 — three-way LLM router for resume generation.

Three paths the router can take, chosen from the active profile's
``llm_routing.resume`` config:

  - "api"        → POST to the configured provider (anthropic, openai,
                   gemini). Returns the response text.
  - "local"      → POST to Ollama at $OLLAMA_HOST (default
                   http://localhost:11434). Quality gate flags
                   ≤4B-parameter models with a warning.
  - "copy_paste" → don't call anything; return the prompt verbatim
                   with a banner instructing the user to paste it
                   into their LLM of choice.

The dashboard's Prompts tab uses the same router output regardless
of path — only the surfaced UX differs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class RouterResult:
    path: str                       # "api" | "local" | "copy_paste"
    provider: Optional[str]         # "anthropic" | "openai" | "gemini" | "ollama" | None
    model: Optional[str]
    output_text: str                # the response (or the prompt itself for copy_paste)
    warnings: list[str]


import re

# Match "<n>b" or "<n>.<m>b" parameter-count tokens (1b, 2b, 3.8b,
# 4b). Models with these tags are flagged as too small to reliably
# tailor a resume; the user gets a yellow warning band.
_SMALL_PARAM_RE = re.compile(
    r"(?:^|[^.\d])([0-4](?:\.\d+)?)b(?:[^a-z0-9]|$)", re.IGNORECASE,
)


def is_small_model(model: str) -> bool:
    m = (model or "").lower()
    return _SMALL_PARAM_RE.search(m) is not None


def route(
    prompt: str,
    *,
    routing_config: dict,
    api_caller: Optional[Callable[[str, str, str], str]] = None,
    ollama_caller: Optional[Callable[[str, str], str]] = None,
) -> RouterResult:
    """Dispatch the prompt through the configured path.

    routing_config shape (from profile YAML's llm_routing.resume):
      {"provider": "anthropic" | "openai" | "gemini" | "local" | "copy_paste",
       "model": "claude-sonnet-4-6" | "gpt-4o" | "gemma2:9b" | ...}

    api_caller(provider, model, prompt) → response_text (injectable
    for tests).
    ollama_caller(model, prompt) → response_text (ditto).
    """
    provider = (routing_config or {}).get("provider") or "copy_paste"
    model = (routing_config or {}).get("model") or ""
    warnings: list[str] = []

    if provider == "copy_paste":
        return RouterResult(
            path="copy_paste",
            provider=None,
            model=None,
            output_text=prompt,
            warnings=warnings,
        )

    if provider == "local":
        if not model:
            warnings.append(
                "No local model configured. Set llm_routing.resume.model "
                "to a pulled Ollama model (e.g. `gemma2:9b`)."
            )
            return RouterResult(
                path="local", provider="ollama", model=None,
                output_text=prompt, warnings=warnings,
            )
        if is_small_model(model):
            warnings.append(
                f"Model {model!r} looks ≤4B parameters. Small models "
                "often produce shallow resume drafts; consider running "
                "the prompt through an API LLM for the final version."
            )
        if ollama_caller is None:
            from engine.resume.router import _default_ollama_call
            ollama_caller = _default_ollama_call
        try:
            text = ollama_caller(model, prompt)
            return RouterResult(
                path="local", provider="ollama", model=model,
                output_text=text, warnings=warnings,
            )
        except Exception as e:
            warnings.append(f"Local call failed: {type(e).__name__}: {e}")
            return RouterResult(
                path="local", provider="ollama", model=model,
                output_text=prompt, warnings=warnings,
            )

    # api path: anthropic / openai / gemini.
    if api_caller is None:
        api_caller = _default_api_call
    try:
        text = api_caller(provider, model, prompt)
        return RouterResult(
            path="api", provider=provider, model=model,
            output_text=text, warnings=warnings,
        )
    except Exception as e:
        warnings.append(f"API call failed: {type(e).__name__}: {e}")
        return RouterResult(
            path="api", provider=provider, model=model,
            output_text=prompt, warnings=warnings,
        )


# --- Default callers (best-effort; tests inject mocks) -----------

def _default_api_call(provider: str, model: str, prompt: str) -> str:
    import os
    if provider == "anthropic":
        import anthropic
        client = anthropic.Anthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        )
        msg = client.messages.create(
            model=model or "claude-sonnet-4-6",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        return "\n".join(block.text for block in msg.content
                         if getattr(block, "type", "") == "text")
    if provider == "openai":
        from openai import OpenAI
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
        resp = client.chat.completions.create(
            model=model or "gpt-4o",
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content or ""
    if provider == "gemini":
        import google.generativeai as genai
        genai.configure(api_key=os.environ.get("GEMINI_API_KEY", ""))
        m = genai.GenerativeModel(model or "gemini-1.5-pro")
        return m.generate_content(prompt).text or ""
    raise ValueError(f"unsupported provider {provider!r}")


def _default_ollama_call(model: str, prompt: str) -> str:
    import os
    import requests
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    r = requests.post(
        f"{host}/api/generate",
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=120,
    )
    r.raise_for_status()
    return r.json().get("response", "")
