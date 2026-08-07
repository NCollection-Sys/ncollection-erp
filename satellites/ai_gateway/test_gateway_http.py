#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP-layer tests for the AI gateway satellite (P5-T02 / #59).

Binds a real server on an ephemeral port and drives it over real HTTP — the
routing, status codes and error mapping are the point, and a test that called
`Gateway.complete()` directly would prove none of them.

Still no network egress: the provider is `mock`, which reaches nothing.

    python3 satellites/ai_gateway/test_gateway_http.py
"""
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gateway import Gateway, Handler  # noqa: E402


def _post(url: str, payload: dict) -> tuple[int, dict]:
    """POST JSON, returning (status, body) for success AND error responses."""
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        method="POST", headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _get(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


class GatewayServerCase(unittest.TestCase):
    """Base: a live gateway on an ephemeral port, with configurable env."""

    ENV: dict = {"NC_AI_PROVIDER": "mock"}

    def setUp(self):
        Handler.gateway = Gateway(dict(self.ENV))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()


class TestHealthAndRouting(GatewayServerCase):

    def test_healthz_reports_provider_and_circuit(self):
        """The satellite contract (§10) requires a health endpoint; it must say
        something useful, not merely 200."""
        status, body = _get(f"{self.base}/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["provider"], "mock")
        self.assertEqual(body["circuit"], "closed")

    def test_unknown_paths_are_404(self):
        self.assertEqual(_get(f"{self.base}/")[0], 404)
        self.assertEqual(_post(f"{self.base}/v1/anything", {"a": 1})[0], 404)


class TestCompletion(GatewayServerCase):

    def test_a_completion_returns_text_and_usage(self):
        status, body = _post(f"{self.base}/v1/complete",
                             {"tenant": "t1", "prompt": "hello there"})
        self.assertEqual(status, 200)
        self.assertIn("text", body)
        self.assertGreater(body["usage"]["total_tokens"], 0)
        self.assertEqual(body["provider"], "mock")

    def test_missing_fields_are_refused_without_echoing_the_body(self):
        """The request body is tenant content; a 400 must not reflect it back."""
        status, body = _post(f"{self.base}/v1/complete", {"prompt": "no tenant"})
        self.assertEqual(status, 400)
        self.assertNotIn("no tenant", json.dumps(body))

    def test_empty_prompt_is_refused(self):
        status, _ = _post(f"{self.base}/v1/complete", {"tenant": "t1", "prompt": ""})
        self.assertEqual(status, 400)


class TestBudgetExhaustion(GatewayServerCase):
    """THE ACCEPTANCE CRITERION: 'budget exhaustion returns a friendly error'."""

    ENV = {"NC_AI_PROVIDER": "mock", "NC_AI_TOKEN_BUDGET": "50",
           "NC_AI_MAX_REQUESTS": "100"}

    def test_exhaustion_is_429_not_500(self):
        """A 500 would read as a platform fault and invite a retry storm."""
        _post(f"{self.base}/v1/complete",
              {"tenant": "t1", "prompt": "x" * 400, "max_tokens": 40})
        status, body = _post(f"{self.base}/v1/complete",
                             {"tenant": "t1", "prompt": "x" * 400, "max_tokens": 40})
        self.assertEqual(status, 429)
        self.assertEqual(body["error"], "ai_budget_exhausted")

    def test_the_message_is_friendly_and_actionable(self):
        """'Friendly' means a human can act on it: what happened, and what to do."""
        _post(f"{self.base}/v1/complete",
              {"tenant": "t1", "prompt": "x" * 400, "max_tokens": 40})
        _, body = _post(f"{self.base}/v1/complete",
                        {"tenant": "t1", "prompt": "x" * 400, "max_tokens": 40})
        message = body["message"]
        self.assertIn("allowance", message)
        self.assertIn("try again later", message)
        self.assertNotIn("Traceback", message)
        # And it tells the caller where they stand.
        self.assertIn("tokens_used", body["usage"])

    def test_one_tenant_cannot_exhaust_another(self):
        for _ in range(3):
            _post(f"{self.base}/v1/complete",
                  {"tenant": "t1", "prompt": "x" * 400, "max_tokens": 40})
        status, _ = _post(f"{self.base}/v1/complete",
                          {"tenant": "t2", "prompt": "hi", "max_tokens": 5})
        self.assertEqual(status, 200)


class TestRateLimit(GatewayServerCase):

    ENV = {"NC_AI_PROVIDER": "mock", "NC_AI_MAX_REQUESTS": "2",
           "NC_AI_TOKEN_BUDGET": "1000000"}

    def test_rate_limit_is_a_distinct_friendly_error(self):
        for _ in range(2):
            _post(f"{self.base}/v1/complete", {"tenant": "t1", "prompt": "hi"})
        status, body = _post(f"{self.base}/v1/complete",
                             {"tenant": "t1", "prompt": "hi"})
        self.assertEqual(status, 429)
        self.assertEqual(body["error"], "ai_rate_limited")
        self.assertIn("wait a moment", body["message"])


class TestOversizedRequest(GatewayServerCase):

    def test_a_huge_body_is_refused_before_it_is_read(self):
        """A prompt is text; anything this large is a bug or an attack."""
        status, _ = _post(f"{self.base}/v1/complete",
                          {"tenant": "t1", "prompt": "x" * (300 * 1024)})
        self.assertEqual(status, 413)


if __name__ == "__main__":
    unittest.main(verbosity=2)
