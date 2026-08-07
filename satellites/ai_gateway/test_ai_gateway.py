#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the AI gateway satellite (P5-T02 / #59).

Stdlib unittest, runnable directly — the satellite is not an Odoo module, so
Odoo's test runner never sees it. Same shape as scripts/ci/test_invariants.py.

    python3 satellites/ai_gateway/test_ai_gateway.py

Every clock is injected. Nothing here sleeps, and nothing here reaches the
network: a test suite that needs either would not run in CI, and a control that
is only exercised in production is not a control.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from limits import (  # noqa: E402
    BudgetExceeded, BudgetLedger, CircuitBreaker, CircuitOpen, RateLimited,
    TenantLimits,
)
from providers import (  # noqa: E402
    MockProvider, ProviderError, build_provider,
)


class FakeClock:
    """A clock the tests move by hand."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestProviderSelection(unittest.TestCase):

    def test_defaults_to_mock_when_unset(self):
        """A misconfigured deployment must produce obviously-fake output rather
        than silently spending money or shipping tenant content somewhere."""
        self.assertIsInstance(build_provider({}), MockProvider)

    def test_anthropic_without_a_key_fails_at_construction(self):
        """Not mid-request: a gateway that starts without credentials and only
        finds out under load has turned a config error into an outage."""
        with self.assertRaises(ProviderError) as caught:
            build_provider({"NC_AI_PROVIDER": "anthropic", "NC_AI_API_KEY": ""})
        self.assertIn("NC_AI_API_KEY", str(caught.exception))

    def test_unknown_provider_is_refused_by_name(self):
        with self.assertRaises(ProviderError) as caught:
            build_provider({"NC_AI_PROVIDER": "definitely-not-a-provider"})
        self.assertIn("known: mock, anthropic", str(caught.exception))

    def test_mock_reports_honest_token_counts(self):
        """The mock must not report zero usage — budget accounting, rate
        limiting and the breaker are all exercised through it, and a stub that
        claims it consumed nothing would make those tests vacuous."""
        completion = MockProvider().complete("x" * 400, max_tokens=50)
        self.assertGreater(completion.input_tokens, 0)
        self.assertGreater(completion.output_tokens, 0)
        self.assertEqual(completion.total_tokens,
                         completion.input_tokens + completion.output_tokens)

    def test_mock_reaches_no_host(self):
        """Belt and braces: the default provider has no egress at all."""
        self.assertEqual(MockProvider().host, "")


class TestBudgetLedger(unittest.TestCase):

    def setUp(self):
        self.clock = FakeClock()
        self.ledger = BudgetLedger(
            TenantLimits(token_budget=1000, max_requests=3, window_seconds=60),
            clock=self.clock)

    def test_under_budget_is_allowed(self):
        self.ledger.check("t1", 100)  # must not raise

    def test_budget_exhaustion_raises_its_own_error(self):
        """THE ACCEPTANCE CRITERION: budget exhaustion must be distinguishable,
        so the gateway can answer 429-with-explanation instead of a 500."""
        self.ledger.record("t1", 950)
        with self.assertRaises(BudgetExceeded) as caught:
            self.ledger.check("t1", 100)
        self.assertIn("token budget exhausted", str(caught.exception))

    def test_the_check_happens_BEFORE_the_call(self):
        """A single oversized request must be refused up front. Charging only
        after success would let one call blow straight through the ceiling."""
        with self.assertRaises(BudgetExceeded):
            self.ledger.check("t1", 5000)

    def test_rate_limit_is_separate_from_budget(self):
        for _ in range(3):
            self.ledger.record("t1", 1)
        with self.assertRaises(RateLimited):
            self.ledger.check("t1", 1)

    def test_tenants_do_not_share_a_budget(self):
        """Cross-tenant bleed here would be a billing and fairness bug."""
        self.ledger.record("t1", 999)
        self.ledger.check("t2", 500)  # must not raise

    def test_window_resets_after_it_elapses(self):
        self.ledger.record("t1", 999)
        with self.assertRaises(BudgetExceeded):
            self.ledger.check("t1", 500)
        self.clock.advance(61)
        self.ledger.check("t1", 500)  # new window

    def test_usage_reports_metadata_only(self):
        self.ledger.record("t1", 42)
        usage = self.ledger.usage("t1")
        self.assertEqual(usage["tokens_used"], 42)
        # No key may carry content — §11 is metadata-only, never bodies.
        for key in usage:
            self.assertNotIn(key, ("prompt", "completion", "text", "body"))


class TestCircuitBreaker(unittest.TestCase):

    def setUp(self):
        self.clock = FakeClock()
        self.breaker = CircuitBreaker(
            failure_threshold=3, reset_seconds=30, clock=self.clock)

    def test_starts_closed(self):
        self.assertEqual(self.breaker.state, "closed")
        self.breaker.before_call()  # must not raise

    def test_opens_only_at_the_threshold(self):
        for _ in range(2):
            self.breaker.record_failure()
        self.assertEqual(self.breaker.state, "closed")
        self.breaker.record_failure()
        self.assertEqual(self.breaker.state, "open")

    def test_open_refuses_calls_with_an_actionable_message(self):
        for _ in range(3):
            self.breaker.record_failure()
        with self.assertRaises(CircuitOpen) as caught:
            self.breaker.before_call()
        self.assertIn("retrying in", str(caught.exception))

    def test_half_opens_after_the_reset_window(self):
        """Recovery must not require a restart."""
        for _ in range(3):
            self.breaker.record_failure()
        self.clock.advance(31)
        self.assertEqual(self.breaker.state, "half_open")
        self.breaker.before_call()  # a probe is allowed through

    def test_success_closes_it_again(self):
        for _ in range(3):
            self.breaker.record_failure()
        self.breaker.record_success()
        self.assertEqual(self.breaker.state, "closed")

    def test_a_success_resets_the_failure_run(self):
        """Consecutive failures, not cumulative — otherwise a long-lived
        gateway trips eventually no matter how healthy the provider is."""
        self.breaker.record_failure()
        self.breaker.record_failure()
        self.breaker.record_success()
        self.breaker.record_failure()
        self.breaker.record_failure()
        self.assertEqual(self.breaker.state, "closed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
