"""Environment settings, price table and model capabilities.

This is the only module that reads the environment (AD-11). Everything else receives a
``Settings`` instance.
"""

from decimal import Decimal

from pydantic_settings import BaseSettings, SettingsConfigDict

# USD per million tokens: (input, output). Estimated, see SPEC.md section 7.
PRICES: dict[str, tuple[Decimal, Decimal]] = {
    "claude-sonnet-5-5": (Decimal("2.00"), Decimal("10.00")),
    "claude-haiku-4-5": (Decimal("1.00"), Decimal("5.00")),
    "claude-opus-5-5": (Decimal("4.00"), Decimal("20.00")),
}

# Which request options a model accepts. A model missing here gets the plain request.
MODEL_CAPABILITIES: dict[str, dict[str, bool]] = {
    "claude-sonnet-5-5": {"effort": True},
    "claude-haiku-4-5": {"effort": False},
    "claude-opus-5-5": {"effort": True},
}


class Settings(BaseSettings):
    """Every variable of SPEC.md section 6, with its default."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    anthropic_api_key: str | None = None
    claude_model: str = "claude-sonnet-5-5"
    claude_effort: str = "low"
    resibo_api_key: str | None = None
    max_upload_mb: int = 20
    max_pages: int = 30
    trusted_proxy_hops: int = 1
    public_max_pages: int = 15
    public_rate_limit_per_hour: int = 5
    daily_budget_usd: Decimal = Decimal("3.00")
    pages_per_chunk: int = 10
    max_output_tokens: int = 16000
    result_ttl_minutes: int = 60
    amount_tolerance: Decimal = Decimal("1.00")
    vat_rate: Decimal = Decimal("0.12")
    log_level: str = "INFO"
    port: int = 8000


def supports_effort(model: str) -> bool:
    return MODEL_CAPABILITIES.get(model, {}).get("effort", False)


def is_priced(model: str) -> bool:
    return model in PRICES


def budget_price(model: str) -> tuple[Decimal, Decimal]:
    """Price used for the daily budget. A model missing from PRICES is charged at the highest
    known input and output price, so the public cap can never be disabled by configuration."""
    if model in PRICES:
        return PRICES[model]
    return (
        max(p[0] for p in PRICES.values()),
        max(p[1] for p in PRICES.values()),
    )


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> Decimal | None:
    """Estimated USD cost, or None for an unpriced model (SPEC.md section 7)."""
    if model not in PRICES:
        return None
    price_in, price_out = PRICES[model]
    cost = (price_in * input_tokens + price_out * output_tokens) / Decimal(1_000_000)
    return cost.quantize(Decimal("0.0001"))
