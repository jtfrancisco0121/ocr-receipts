import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from resibo.config import PRICES, Settings
from resibo.models import ErrorCode, ResiboError
from resibo.ratelimit import (
    DailyBudget,
    SlidingWindowLimiter,
    budget_hooks,
    client_ip,
    reservation_cost,
)


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


# client_ip
def test_client_ip_takes_rightmost_entry_by_default():
    headers = {"X-Forwarded-For": "6.6.6.6, 10.0.0.1, 34.1.2.3"}
    assert client_ip(headers, "172.16.0.1", 1) == "34.1.2.3"
    assert client_ip(headers, "172.16.0.1", 2) == "10.0.0.1"


def test_client_ip_never_trusts_the_leftmost_entry():
    a = client_ip({"x-forwarded-for": "1.1.1.1, 34.1.2.3"}, "p", 1)
    b = client_ip({"x-forwarded-for": "2.2.2.2, 34.1.2.3"}, "p", 1)
    assert a == b == "34.1.2.3"


def test_client_ip_falls_back_to_the_peer():
    assert client_ip({}, "172.16.0.1", 1) == "172.16.0.1"
    assert client_ip({"x-forwarded-for": "9.9.9.9"}, "172.16.0.1", 2) == "172.16.0.1"
    assert client_ip({"x-forwarded-for": " , "}, None, 1) == "unknown"


# sliding window
def test_limiter_allows_k_then_rejects_with_retry_after():
    clock = Clock()
    limiter = SlidingWindowLimiter(2, 3600, clock)
    limiter.hit("a")
    clock.advance(seconds=600)
    limiter.hit("a")
    with pytest.raises(ResiboError) as exc:
        limiter.hit("a")
    assert exc.value.code == ErrorCode.RATE_LIMITED
    assert exc.value.headers["Retry-After"] == "3000"  # until the oldest hit leaves the window
    limiter.hit("b")  # other clients are independent
    clock.advance(seconds=3000)
    limiter.hit("a")  # the oldest one expired


def test_limiter_is_exact_under_threads():
    limiter = SlidingWindowLimiter(7, 3600, Clock())
    outcomes = []
    barrier = threading.Barrier(40)

    def worker():
        barrier.wait()
        try:
            limiter.hit("ip")
            outcomes.append(True)
        except ResiboError:
            outcomes.append(False)

    threads = [threading.Thread(target=worker) for _ in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count(True) == 7 and outcomes.count(False) == 33


# daily budget
def test_budget_reserve_settle_and_exhaustion():
    budget = DailyBudget(D("1.00"), Clock())
    token = budget.reserve(D("0.60"))
    assert budget.spent == D("0.60")
    budget.settle(token, D("0.10"))  # actual cost replaces the reservation
    assert budget.spent == D("0.10")
    budget.reserve(D("0.90"))
    with pytest.raises(ResiboError) as exc:
        budget.reserve(D("0.01"))
    assert exc.value.code == ErrorCode.DEMO_BUDGET_EXHAUSTED


def test_budget_failed_call_without_usage_keeps_the_reservation():
    budget = DailyBudget(D("1.00"), Clock())
    token = budget.reserve(D("0.40"))
    budget.settle(token, None)
    assert budget.spent == D("0.40")


def test_budget_never_exceeded_under_threads():
    budget = DailyBudget(D("1.00"), Clock())
    outcomes = []
    barrier = threading.Barrier(30)

    def worker():
        barrier.wait()
        try:
            budget.reserve(D("0.15"))  # 6 fit in 1.00, never 7
            outcomes.append(True)
        except ResiboError as exc:
            assert exc.code == ErrorCode.DEMO_BUDGET_EXHAUSTED
            outcomes.append(False)

    threads = [threading.Thread(target=worker) for _ in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count(True) == 6 and outcomes.count(False) == 24
    assert budget.spent == D("0.90") and budget.spent <= D("1.00")


def test_budget_resets_on_the_utc_day_under_the_lock():
    clock = Clock()
    clock.now = datetime(2026, 11, 23, 23, 59, tzinfo=UTC)
    budget = DailyBudget(D("1.00"), clock)
    token = budget.reserve(D("1.00"))
    clock.advance(minutes=2)  # now 2026-11-24 UTC
    assert budget.spent == D("0")
    budget.reserve(D("1.00"))  # a fresh day
    budget.settle(token, D("0.01"))  # a stale reservation does not touch the new day
    assert budget.spent == D("1.00")


def test_reservation_cost_formula_and_unpriced_model_uses_highest_price():
    s = Settings(max_output_tokens=16000)
    # 15 pages x 3,000 input tokens at $2/M + 16,000 output tokens at $10/M
    assert reservation_cost(15, s) == D("0.09") + D("0.16")
    unknown = Settings(claude_model="mystery-model", max_output_tokens=16000)
    top_in = max(p[0] for p in PRICES.values())
    top_out = max(p[1] for p in PRICES.values())
    assert reservation_cost(10, unknown) == (top_in * 30000 + top_out * 16000) / D(1_000_000)
    assert reservation_cost(10, unknown) > reservation_cost(10, s)


def test_budget_hooks_settle_with_actual_tokens_or_keep_reservation():
    s = Settings()
    budget = DailyBudget(D("3.00"), Clock())
    reserve, settle = budget_hooks(budget, s)
    token = reserve(2)
    settle(token, 1_000_000, 0)  # $2.00 at the sonnet price
    assert budget.spent == D("2.00")
    token = reserve(1)
    before = budget.spent
    settle(token, None, None)
    assert budget.spent == before
