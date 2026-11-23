from decimal import Decimal

from resibo import config
from resibo.config import PRICES, Settings, budget_price, estimate_cost, supports_effort


def test_budget_price_unknown_model_is_highest_known_never_zero():
    price_in, price_out = budget_price("unknown-model")
    assert price_in == max(p[0] for p in PRICES.values())
    assert price_out == max(p[1] for p in PRICES.values())
    assert price_in > 0 and price_out > 0


def test_budget_price_known_model_is_its_own():
    assert budget_price("claude-haiku-4-5") == PRICES["claude-haiku-4-5"]
    assert budget_price("claude-sonnet-5-5") == (Decimal("2.00"), Decimal("10.00"))


def test_estimate_cost_unknown_model_is_none():
    assert estimate_cost("unknown-model", 1000, 1000) is None
    assert estimate_cost("claude-sonnet-5-5", 1_000_000, 1_000_000) == Decimal("12.0000")


def test_effort_support_map():
    assert supports_effort("claude-sonnet-5-5")
    assert not supports_effort("claude-haiku-4-5")
    assert not supports_effort("unknown-model")


def test_settings_defaults(monkeypatch):
    monkeypatch.delenv("CLAUDE_MODEL", raising=False)
    s = Settings()
    assert s.claude_model == "claude-sonnet-5-5"
    assert s.public_max_pages == 15 and s.max_pages == 30
    assert s.amount_tolerance == Decimal("1.00")
    assert s.anthropic_api_key is None
    assert config.is_priced(s.claude_model)


def test_settings_read_environment(monkeypatch):
    monkeypatch.setenv("PAGES_PER_CHUNK", "4")
    assert Settings().pages_per_chunk == 4
