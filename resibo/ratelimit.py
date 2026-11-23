"""Client IP, per-IP sliding window limiter and the global daily budget.

Both limiters take an injected UTC clock (AD-9) and do check-and-update as one atomic step under
a lock, because extraction runs in a threadpool.
"""

import math
import threading
from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from resibo.config import Settings, budget_price
from resibo.models import ErrorCode, ResiboError

INPUT_TOKENS_PER_PAGE = 3000  # worst-case estimate used to reserve budget


def client_ip(headers: Mapping[str, str], peer: str | None, hops: int) -> str:
    """The ``hops``-th entry of X-Forwarded-For counted from the right, else the socket peer.
    The leftmost entry is client-controlled and is never trusted (AD-12)."""
    forwarded = None
    for name, value in headers.items():
        if name.lower() == "x-forwarded-for":
            forwarded = value
            break
    if forwarded and hops >= 1:
        entries = [part.strip() for part in forwarded.split(",") if part.strip()]
        if len(entries) >= hops:
            return entries[-hops]
    return peer or "unknown"


class SlidingWindowLimiter:
    """At most ``limit`` hits per key within ``window_seconds``."""

    def __init__(
        self,
        limit: int,
        window_seconds: float,
        clock: Callable[[], datetime] | None = None,
        message: str = "Too many requests. Please wait a bit and try again.",
    ):
        self._limit = limit
        self._window = timedelta(seconds=window_seconds)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._message = message
        self._hits: dict[str, deque[datetime]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str) -> None:
        """Count one request or raise ``RATE_LIMITED`` with ``Retry-After``."""
        with self._lock:
            now = self._clock()
            hits = self._hits[key]
            while hits and hits[0] <= now - self._window:
                hits.popleft()
            if len(hits) >= self._limit:
                wait = max(1, math.ceil((hits[0] + self._window - now).total_seconds()))
                raise ResiboError(
                    ErrorCode.RATE_LIMITED,
                    f"{self._message} Try again in {wait} seconds.",
                    headers={"Retry-After": str(wait)},
                )
            hits.append(now)
            if len(self._hits) > 10_000:  # keep memory bounded
                for stale in [k for k, v in self._hits.items() if not v and k != key]:
                    del self._hits[stale]


@dataclass(frozen=True)
class Reservation:
    day: date
    amount: Decimal


class DailyBudget:
    """Estimated USD spend per UTC day for public requests, reserved per Claude call."""

    def __init__(self, budget_usd: Decimal, clock: Callable[[], datetime] | None = None):
        self._budget = Decimal(budget_usd)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._day = self._clock().astimezone(UTC).date()
        self._spent = Decimal(0)
        self._lock = threading.Lock()

    def _roll(self) -> date:
        today = self._clock().astimezone(UTC).date()
        if today != self._day:
            self._day, self._spent = today, Decimal(0)
        return today

    @property
    def spent(self) -> Decimal:
        with self._lock:
            self._roll()
            return self._spent

    def reserve(self, amount: Decimal) -> Reservation:
        with self._lock:
            day = self._roll()
            if self._spent + amount > self._budget:
                raise ResiboError(
                    ErrorCode.DEMO_BUDGET_EXHAUSTED,
                    "The live demo has reached today's limit. Try the sample, or run it locally.",
                )
            self._spent += amount
            return Reservation(day, amount)

    def settle(self, reservation: Reservation, actual: Decimal | None) -> None:
        """Replace the reservation with the actual cost; None keeps the reservation."""
        if actual is None:
            return
        with self._lock:
            if self._roll() != reservation.day:
                return  # the reserved day is over; its total was already reset
            self._spent = max(Decimal(0), self._spent - reservation.amount + actual)


def reservation_cost(pages: int, settings: Settings) -> Decimal:
    """Worst case for one call: ``pages`` x 3,000 input tokens plus MAX_OUTPUT_TOKENS output."""
    price_in, price_out = budget_price(settings.claude_model)
    return (
        price_in * pages * INPUT_TOKENS_PER_PAGE + price_out * settings.max_output_tokens
    ) / Decimal(1_000_000)


def actual_cost(settings: Settings, input_tokens: int, output_tokens: int) -> Decimal:
    price_in, price_out = budget_price(settings.claude_model)
    return (price_in * input_tokens + price_out * output_tokens) / Decimal(1_000_000)


def budget_hooks(budget: DailyBudget, settings: Settings):
    """``(reserve, settle)`` for ``CallPolicy``: every Claude call is reserved and settled."""

    def reserve(pages: int) -> Reservation:
        return budget.reserve(reservation_cost(pages, settings))

    def settle(token: Reservation, input_tokens: int | None, output_tokens: int | None) -> None:
        if input_tokens is None or output_tokens is None:
            budget.settle(token, None)
        else:
            budget.settle(token, actual_cost(settings, input_tokens, output_tokens))

    return reserve, settle
