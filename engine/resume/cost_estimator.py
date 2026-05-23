"""Spec 8 TASK 3 — resume-generation cost estimator.

Pure look-up table sourced from Appendix C of the GitHub Release
Spec. Rates are in USD per 1M tokens at the time the spec was
written (early 2026); the doc says they're for orientation, not
contract — refresh if the table goes stale.
"""
from __future__ import annotations

from dataclasses import dataclass


# Token-per-request assumption: ~3K input, ~1K output (Appendix C).
_AVG_INPUT_TOKENS = 3000
_AVG_OUTPUT_TOKENS = 1000


@dataclass(frozen=True)
class ProviderRate:
    input_per_million: float
    output_per_million: float


# Rates in USD per 1,000,000 tokens (Appendix C).
RATES: dict[str, ProviderRate] = {
    "anthropic_sonnet": ProviderRate(3.00, 15.00),
    "anthropic_opus":   ProviderRate(15.00, 75.00),
    "openai_gpt4o":     ProviderRate(2.50, 10.00),
    "openai_gpt4o_mini": ProviderRate(0.15, 0.60),
    "gemini_pro":       ProviderRate(1.25, 5.00),
    "gemini_flash":     ProviderRate(0.075, 0.30),
    "local":            ProviderRate(0.0, 0.0),
}


@dataclass(frozen=True)
class CostEstimate:
    provider: str
    per_app_usd: float
    monthly_usd: float
    annual_usd: float


def per_app_cost(provider: str) -> float:
    rate = RATES.get(provider)
    if rate is None:
        raise ValueError(
            f"unknown provider {provider!r}; "
            f"options: {sorted(RATES)}"
        )
    return round(
        (_AVG_INPUT_TOKENS  / 1_000_000) * rate.input_per_million
        + (_AVG_OUTPUT_TOKENS / 1_000_000) * rate.output_per_million,
        4,
    )


def estimate(
    provider: str,
    *,
    apps_per_week: int,
) -> CostEstimate:
    per_app = per_app_cost(provider)
    monthly = round(per_app * apps_per_week * 4.33, 2)
    annual = round(monthly * 12, 2)
    return CostEstimate(
        provider=provider,
        per_app_usd=per_app,
        monthly_usd=monthly,
        annual_usd=annual,
    )


def estimate_all(apps_per_week: int) -> list[CostEstimate]:
    return [estimate(p, apps_per_week=apps_per_week) for p in RATES]
