"""Bounded distributed rate limiting.

In-memory implementation for single-instance use. For multi-instance
deployments, replace with a shared store (Redis, MongoDB, etc.).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class RateLimitBucket:
    """Token bucket for rate limiting."""

    capacity: int
    refill_rate: float  # tokens per second
    tokens: float = 0.0
    last_refill: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        self.tokens = float(self.capacity)

    def consume(self, tokens: int = 1) -> bool:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False


class RateLimiter:
    """Simple in-memory rate limiter keyed by (principal, operation)."""

    def __init__(self) -> None:
        self._buckets: dict[str, RateLimitBucket] = {}
        self._defaults: dict[str, tuple[int, float]] = {}

    def configure(self, operation: str, capacity: int, refill_rate: float) -> None:
        self._defaults[operation] = (capacity, refill_rate)

    def _key(self, principal: str, operation: str) -> str:
        return f"{principal}:{operation}"

    def check(self, principal: str, operation: str) -> bool:
        key = self._key(principal, operation)
        if key not in self._buckets:
            cap, rate = self._defaults.get(operation, (100, 1.0))
            self._buckets[key] = RateLimitBucket(capacity=cap, refill_rate=rate)
        return self._buckets[key].consume()
