# -*- coding: utf-8 -*-
"""Per-tenant budget, rate limit and circuit breaker (P5-T02 / #59).

These are the controls that make the gateway a *choke point* rather than a
proxy. ARCHITECTURE_SECURITY §11 names the risks they answer: "runaway spend
against a metered API", "a held-open streaming call starving the cron worker",
and provider outage.

WHY STATE LIVES IN MEMORY
-------------------------
The satellite holds **no database credentials** — that is the entire point of
the topology (ARCHITECTURE_DATA_PLATFORM §10.4: "it receives context, never
fetches it"). So there is nowhere durable to keep counters without handing this
process exactly the access the design removed.

The honest consequence, stated rather than buried: **a restart resets budgets and
the breaker.** For a per-day token budget that is a real weakness — a crash-loop
could multiply the daily allowance. It is accepted here because:

* the alternative reintroduces DB credentials into the egress path, which is a
  strictly worse trade, and
* the hard ceiling that actually protects the bill is the provider-side spend cap,
  which no restart can reset.

If durable budgets are needed later, the correct place is the *tenant* side
(P5-T03), which already has a database and is inside the isolation boundary —
not here. Recorded so the next person does not "fix" it by giving the satellite
a database.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


class BudgetExceeded(RuntimeError):
    """Raised when a tenant is out of tokens for the current window.

    Distinct from every other failure because the ticket's acceptance criterion
    turns on it: "budget exhaustion returns a friendly error". The gateway maps
    this to HTTP 429 with an explanatory message, never a 500.
    """


class RateLimited(RuntimeError):
    """Raised when a tenant exceeds its request rate."""


class CircuitOpen(RuntimeError):
    """Raised when the provider is considered down and calls are being refused."""


@dataclass
class _Window:
    """A fixed window of consumption for one tenant."""
    started: float
    tokens: int = 0
    requests: int = 0


@dataclass
class TenantLimits:
    """The knobs, per tenant. Defaults are the spike's calibration starting
    points (§4) — deliberately conservative, and expected to be tuned with real
    usage rather than guessed more precisely now."""
    token_budget: int = 100_000     # per window
    max_requests: int = 60          # per window
    window_seconds: int = 3600


class BudgetLedger:
    """Per-tenant token budget + request rate, over a fixed window.

    Thread-safe because the gateway serves requests concurrently; a lock is
    cheap here and a lost update would silently overspend.
    """

    def __init__(self, limits: TenantLimits | None = None,
                 clock=time.monotonic) -> None:
        self._limits = limits or TenantLimits()
        self._clock = clock          # injectable: tests must not sleep
        self._lock = threading.Lock()
        self._windows: dict[str, _Window] = {}

    def _window_for(self, tenant: str) -> _Window:
        now = self._clock()
        window = self._windows.get(tenant)
        if window is None or now - window.started >= self._limits.window_seconds:
            window = _Window(started=now)
            self._windows[tenant] = window
        return window

    def check(self, tenant: str, estimated_tokens: int) -> None:
        """Refuse BEFORE calling the provider. Raises BudgetExceeded/RateLimited.

        Checking up front is what makes the budget meaningful: charging only
        after a successful call would let a tenant blow through the ceiling with
        one very large request.
        """
        with self._lock:
            window = self._window_for(tenant)
            if window.requests + 1 > self._limits.max_requests:
                raise RateLimited(
                    "rate limit reached: %s requests per %ss"
                    % (self._limits.max_requests, self._limits.window_seconds))
            if window.tokens + estimated_tokens > self._limits.token_budget:
                raise BudgetExceeded(
                    "token budget exhausted: %s tokens per %ss"
                    % (self._limits.token_budget, self._limits.window_seconds))

    def record(self, tenant: str, tokens: int) -> None:
        """Commit ACTUAL usage after the provider answered."""
        with self._lock:
            window = self._window_for(tenant)
            window.tokens += tokens
            window.requests += 1

    def usage(self, tenant: str) -> dict:
        """Metadata for the health/usage endpoint. No content, ever."""
        with self._lock:
            window = self._window_for(tenant)
            return {
                "tokens_used": window.tokens,
                "token_budget": self._limits.token_budget,
                "requests_used": window.requests,
                "max_requests": self._limits.max_requests,
                "window_seconds": self._limits.window_seconds,
            }


@dataclass
class CircuitBreaker:
    """Stop hammering a provider that is already failing.

    Classic three-state breaker. `half_open` matters: without it, recovery
    requires either a restart or a flood of retries, and the first is not
    something a satellite should need.
    """

    failure_threshold: int = 5
    reset_seconds: float = 60.0
    clock: object = field(default=time.monotonic)

    _failures: int = field(default=0, init=False)
    _opened_at: float | None = field(default=None, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    @property
    def state(self) -> str:
        with self._lock:
            return self._state_unlocked()

    def _state_unlocked(self) -> str:
        if self._opened_at is None:
            return "closed"
        if self.clock() - self._opened_at >= self.reset_seconds:
            return "half_open"
        return "open"

    def before_call(self) -> None:
        with self._lock:
            if self._state_unlocked() == "open":
                raise CircuitOpen(
                    "provider circuit is open after %s consecutive failures; "
                    "retrying in %ss" % (self.failure_threshold, self.reset_seconds))

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._opened_at = self.clock()
