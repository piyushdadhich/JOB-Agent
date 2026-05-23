"""LLM client wrapper around Ollama for the job-agent system.

Provides a single synchronous interface (LLMClient) that all agents use
for text generation, classification, JSON extraction, and scoring.
Centralizing Ollama access here means individual agents stay ignorant
of the underlying model/service details.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

import ollama
import requests
from dotenv import load_dotenv


# --- Exceptions -------------------------------------------------------------

class OllamaConnectionError(Exception):
    """Raised when the Ollama service cannot be reached."""


class OllamaModelError(Exception):
    """Raised when the requested model is not available on the Ollama host."""


class LLMParseError(Exception):
    """Raised when the model's response cannot be parsed into the expected shape.

    The raw model response is attached as the `raw` attribute for debugging.
    """

    def __init__(self, message: str, raw: str = ""):
        super().__init__(message)
        self.raw = raw


# --- Logging setup ----------------------------------------------------------

# Logger is module-level so repeated LLMClient() construction doesn't duplicate
# handlers. The logs/ directory is assumed to exist (project convention).
_LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "llm_client.log"
logger = logging.getLogger("llm_client")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh = logging.FileHandler(_LOG_PATH, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)


# --- Helpers ----------------------------------------------------------------

def _extract_json_blob(text: str) -> str:
    """Pull the first {...} JSON object out of a possibly-noisy response.

    Gemma often wraps JSON in ```json fences or adds prose. We strip fences
    and grab the outermost braces. Returns the stripped input if no braces
    are found so json.loads can produce a meaningful error.
    """
    stripped = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.DOTALL)
    if fence:
        stripped = fence.group(1).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        return stripped[start : end + 1]
    return stripped


# --- Main client ------------------------------------------------------------

class LLMClient:
    """Synchronous wrapper around the local Ollama service.

    All methods are blocking. This is intentional — the job-agent is a
    single-user tool and concurrency isn't needed.
    """

    def __init__(self, model: Optional[str] = None, host: Optional[str] = None):
        load_dotenv()  # No-op if .env is missing.
        self.model = model or os.getenv("OLLAMA_MODEL", "gemma4:e4b")
        self.host = host or os.getenv("OLLAMA_HOST", "http://localhost:11434")
        # A per-instance Ollama client bound to the configured host.
        self._client = ollama.Client(host=self.host)

    # -- Internal: single point where we actually talk to Ollama -----------
    def _chat(self, messages: list[dict], temperature: float) -> str:
        """Call Ollama and return the assistant text, translating errors."""
        try:
            response = self._client.chat(
                model=self.model,
                messages=messages,
                options={"temperature": temperature},
            )
        except ConnectionError as e:
            logger.error("Connection error talking to Ollama: %s", e)
            raise OllamaConnectionError(
                f"Cannot reach Ollama at {self.host}. "
                "Make sure the Ollama service is running."
            ) from e
        except ollama.ResponseError as e:
            # ResponseError covers both missing-model (404) and other API errors.
            msg = str(e).lower()
            if "not found" in msg or "no such" in msg or getattr(e, "status_code", None) == 404:
                logger.error("Model %s missing on host: %s", self.model, e)
                raise OllamaModelError(
                    f"Model '{self.model}' is not available on {self.host}. "
                    f"Pull it with: ollama pull {self.model}"
                ) from e
            logger.error("Ollama API error: %s", e)
            raise
        except Exception as e:
            # Requests/httpx may raise their own connection errors; treat any
            # error whose repr mentions connection/refused as a connect failure.
            if any(tok in repr(e).lower() for tok in ("connection", "refused", "connect")):
                logger.error("Connection-like error talking to Ollama: %s", e)
                raise OllamaConnectionError(
                    f"Cannot reach Ollama at {self.host}. "
                    "Make sure the Ollama service is running."
                ) from e
            logger.error("Unexpected error in Ollama call: %s", e)
            raise

        return response["message"]["content"]

    # -- Public API --------------------------------------------------------

    def generate(self, prompt: str, system: Optional[str] = None,
                 temperature: float = 0.3) -> str:
        """Send a prompt to the model and return its text response.

        Args:
            prompt: The user message.
            system: Optional system prompt to steer the model.
            temperature: Sampling temperature. Default 0.3 for focused output.

        Returns:
            The assistant's response text (stripped of surrounding whitespace).
        """
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        text = self._chat(messages, temperature).strip()
        logger.info("generate() ok model=%s chars=%d", self.model, len(text))
        return text

    def generate_chat(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        num_predict: Optional[int] = None,
        num_ctx: Optional[int] = None,
        model: Optional[str] = None,
        keep_alive: "int | str" = -1,
        think: Optional[bool] = None,
    ) -> dict:
        """Call Ollama /api/chat with system+user message separation.

        Returns the full Ollama response dict including timing fields
        (prompt_eval_duration, eval_duration, total_duration) so
        callers can log latency breakdowns and verify prefix caching.

        Why this exists alongside generate(): generate() goes through
        the ollama Python SDK which does not expose num_predict /
        num_ctx / keep_alive forwarding or the timing fields needed
        to verify prefix-cache hits. generate_chat() talks to
        /api/chat directly via requests so the pipeline can:
          - cap output with num_predict
          - pin context window per stage with num_ctx
          - keep model resident with keep_alive=-1 to preserve the
            prefix-cache KV tensors across sequential calls
          - read prompt_eval_duration to confirm caching fired
        """
        url = f"{self.host}/api/chat"
        options: dict = {"temperature": temperature}
        if num_predict is not None:
            options["num_predict"] = num_predict
        if num_ctx is not None:
            options["num_ctx"] = num_ctx
        payload = {
            "model": model or self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "keep_alive": keep_alive,
            "options": options,
        }
        # Ollama puts `think` at top level (not inside options).
        # When set, thinking-capable models (Gemma 3, Gemma 4 E4B, etc.)
        # will obey. Without it, gemma4:e4b silently consumes the
        # num_predict budget on a thinking trace and returns empty
        # content - which is exactly what bricked the v2.3 fast eval.
        if think is not None:
            payload["think"] = think

        response = requests.post(url, json=payload, timeout=600)
        response.raise_for_status()
        data = response.json()
        data["response"] = (
            data.get("message", {}).get("content", "")
        )
        return data

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        **kwargs,
    ) -> str:
        """Convenience wrapper around generate_chat() returning text only."""
        data = self.generate_chat(system_prompt, user_prompt, **kwargs)
        return data["response"]

    def classify(self, text: str, categories: list[str],
                 context: Optional[str] = None) -> dict:
        """Classify `text` into one of `categories`.

        Returns a dict: {"category": <str from categories>, "confidence": <float 0-1>}.
        Retries once on parse/validation failure before raising LLMParseError.
        """
        cat_list = ", ".join(f'"{c}"' for c in categories)
        context_block = f"\nBackground context:\n{context}\n" if context else ""
        system = (
            "You are a precise classifier. Respond with ONLY a JSON object "
            'of the form {"category": "...", "confidence": 0.0}. '
            "The category must be exactly one of the provided options. "
            "Confidence is a number between 0.0 and 1.0."
        )
        prompt = (
            f"Categories: [{cat_list}]{context_block}\n"
            f"Text to classify:\n{text}\n\n"
            "Return only the JSON object."
        )

        last_raw = ""
        for attempt in (1, 2):
            raw = self._chat(
                [{"role": "system", "content": system},
                 {"role": "user", "content": prompt}],
                temperature=0.1,
            )
            last_raw = raw
            try:
                data = json.loads(_extract_json_blob(raw))
                category = data["category"]
                confidence = float(data["confidence"])
                if category not in categories:
                    raise ValueError(f"category {category!r} not in allowed list")
                if not 0.0 <= confidence <= 1.0:
                    raise ValueError(f"confidence {confidence} out of range")
                logger.info("classify() ok model=%s category=%s", self.model, category)
                return {"category": category, "confidence": confidence}
            except (ValueError, KeyError, json.JSONDecodeError) as e:
                if attempt == 1:
                    logger.warning("classify() parse failed, retrying: %s", e)
                    continue
                logger.error("classify() failed after retry: %s", e)
                raise LLMParseError(
                    f"Could not parse classification response: {e}", raw=last_raw
                ) from e

    def extract_json(self, text: str, schema_description: str) -> dict:
        """Extract structured data from `text` per `schema_description`.

        Returns the parsed JSON as a dict. Retries once with a stricter
        prompt before raising LLMParseError.
        """
        base_system = (
            "You are a structured-data extractor. Return ONLY a single JSON "
            "object. No prose, no markdown fences, no commentary."
        )
        strict_system = base_system + (
            " Previous attempt returned invalid JSON. Return raw JSON only, "
            "starting with { and ending with }. Use null for missing values."
        )
        user_prompt = (
            f"Extraction instructions: {schema_description}\n\n"
            f"Source text:\n{text}"
        )

        last_raw = ""
        for attempt in (1, 2):
            system = base_system if attempt == 1 else strict_system
            raw = self._chat(
                [{"role": "system", "content": system},
                 {"role": "user", "content": user_prompt}],
                temperature=0.1,
            )
            last_raw = raw
            try:
                data = json.loads(_extract_json_blob(raw))
                if not isinstance(data, dict):
                    raise ValueError("expected a JSON object at top level")
                logger.info("extract_json() ok model=%s keys=%s",
                            self.model, list(data.keys()))
                return data
            except (ValueError, json.JSONDecodeError) as e:
                if attempt == 1:
                    logger.warning("extract_json() parse failed, retrying strict: %s", e)
                    continue
                logger.error("extract_json() failed after retry: %s", e)
                raise LLMParseError(
                    f"Could not parse JSON extraction: {e}", raw=last_raw
                ) from e

    def score(self, text: str, criteria: str,
              scale: tuple = (1, 10)) -> dict:
        """Score `text` against `criteria` on the given integer scale.

        Returns {"score": <int>, "reasoning": <str>}. Retries once on
        invalid response.
        """
        lo, hi = scale
        system = (
            f"You rate text on a {lo}-{hi} integer scale against given criteria. "
            'Respond with ONLY a JSON object: {"score": <int>, "reasoning": "<one or two sentences>"}.'
        )
        prompt = (
            f"Criteria: {criteria}\n\n"
            f"Text:\n{text}\n\n"
            f"Score must be an integer between {lo} and {hi} inclusive."
        )

        last_raw = ""
        for attempt in (1, 2):
            raw = self._chat(
                [{"role": "system", "content": system},
                 {"role": "user", "content": prompt}],
                temperature=0.2,
            )
            last_raw = raw
            try:
                data = json.loads(_extract_json_blob(raw))
                score_val = int(data["score"])
                reasoning = str(data["reasoning"]).strip()
                if not lo <= score_val <= hi:
                    raise ValueError(f"score {score_val} out of range [{lo},{hi}]")
                if not reasoning:
                    raise ValueError("empty reasoning")
                logger.info("score() ok model=%s score=%d", self.model, score_val)
                return {"score": score_val, "reasoning": reasoning}
            except (ValueError, KeyError, json.JSONDecodeError) as e:
                if attempt == 1:
                    logger.warning("score() parse failed, retrying: %s", e)
                    continue
                logger.error("score() failed after retry: %s", e)
                raise LLMParseError(
                    f"Could not parse score response: {e}", raw=last_raw
                ) from e
