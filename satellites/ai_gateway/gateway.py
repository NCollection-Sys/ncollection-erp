#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI gateway satellite — the single choke point for every LLM call (P5-T02 / #59).

WHAT THIS PROCESS IS, AND IS NOT
--------------------------------
It is the ONLY component on the platform permitted to call an LLM provider
(ARCHITECTURE_SECURITY §11). It holds the provider API keys.

It holds **no database credentials at all** — not the admin DB's, not any
tenant's. That is the whole point of the satellite topology
(ARCHITECTURE_DATA_PLATFORM §10.4: "it receives context, never fetches it"), and
it is what lets §11's "no tenant DB makes an outbound call" survive a feature
that is outbound traffic carrying tenant business data. Context arrives already
built and already PII-sanitised, from the tenant database that owns it and
cannot read any other.

If you are about to give this process a database connection: don't. The design
considered it (AI_PLATFORM_DESIGN §1.2, Option B) and rejected it as trading one
security control for a worse one.

LOGGING IS METADATA ONLY — NEVER BODIES
---------------------------------------
§11 is explicit, and it is consistent with the ncollection_auth retention stance
(#219/#261). Prompts and completions are tenant business content; this process
must not persist them anywhere, including in an exception trace. Provider errors
are therefore reduced to a status code before they are ever logged, because a
provider's error body can echo the prompt back.

RUN
    NC_AI_PROVIDER=mock python3 gateway.py        # default; reaches nothing
    NC_AI_PROVIDER=anthropic NC_AI_API_KEY=... python3 gateway.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from limits import (  # noqa: E402
    BudgetExceeded, BudgetLedger, CircuitBreaker, CircuitOpen, RateLimited,
    TenantLimits,
)
from providers import ProviderError, build_provider  # noqa: E402

# Structured JSON logs, per the satellite contract (§10: "health endpoint,
# structured JSON logs, restart-safe, resource-limited").
logging.basicConfig(
    level=os.environ.get("NC_AI_LOG_LEVEL", "INFO"),
    format='{"ts":"%(asctime)s","level":"%(levelname)s","msg":%(message)s}',
)
_log = logging.getLogger("ai_gateway")

MAX_REQUEST_BYTES = 256 * 1024   # a prompt is text; anything larger is a bug or an attack
DEFAULT_MAX_TOKENS = 1024


def _limits_from_env(env: dict) -> TenantLimits:
    """Budgets are configuration, not constants — §4 calls the shipped numbers
    'calibration starting points' expected to be tuned against real usage."""
    return TenantLimits(
        token_budget=int(env.get("NC_AI_TOKEN_BUDGET", 100_000)),
        max_requests=int(env.get("NC_AI_MAX_REQUESTS", 60)),
        window_seconds=int(env.get("NC_AI_WINDOW_SECONDS", 3600)),
    )


class Gateway:
    """Provider + controls. Separated from the HTTP layer so it is testable
    without binding a socket."""

    def __init__(self, env: dict | None = None) -> None:
        env = os.environ if env is None else env
        self.provider = build_provider(env)
        self.ledger = BudgetLedger(_limits_from_env(env))
        self.breaker = CircuitBreaker(
            failure_threshold=int(env.get("NC_AI_BREAKER_THRESHOLD", 5)),
            reset_seconds=float(env.get("NC_AI_BREAKER_RESET", 60)),
        )

        # A budget smaller than one default request's WORST CASE refuses every
        # caller that does not set max_tokens explicitly — with "budget
        # exhausted" on the very first request, which reads like a quota problem
        # rather than the misconfiguration it is. The check reserves
        # prompt + max_tokens up front (a ceiling charged only on success is not
        # a ceiling), so this is inherent, not incidental.
        #
        # A warning, not a hard failure: a deliberately small budget is
        # legitimate for a restricted tier whose callers pass small max_tokens.
        # Found by the #59 proof script, which set a 60-token budget and could
        # not complete a single default request.
        budget = self.ledger.usage("_startup_probe")["token_budget"]
        if budget < DEFAULT_MAX_TOKENS:
            _log.warning(json.dumps({
                "event": "config_warning",
                "detail": ("NC_AI_TOKEN_BUDGET (%d) is below the default "
                           "max_tokens (%d): any request that does not set "
                           "max_tokens explicitly will be refused as budget-"
                           "exhausted on its first call"
                           % (budget, DEFAULT_MAX_TOKENS)),
            }))

    def complete(self, tenant: str, prompt: str, max_tokens: int) -> dict:
        """The one path to a provider. Returns a JSON-able dict.

        Order matters. The breaker is consulted before the budget so a provider
        outage does not silently burn a tenant's allowance on calls that cannot
        succeed.
        """
        self.breaker.before_call()

        # ~4 chars/token, matching the mock's estimate. Checking BEFORE the call
        # is what makes the ceiling real: charging only on success would let one
        # very large request blow straight through it.
        estimated = max(1, len(prompt) // 4) + max_tokens
        self.ledger.check(tenant, estimated)

        started = time.monotonic()
        try:
            completion = self.provider.complete(prompt, max_tokens)
        except ProviderError:
            self.breaker.record_failure()
            raise
        self.breaker.record_success()

        self.ledger.record(tenant, completion.total_tokens)
        elapsed_ms = int((time.monotonic() - started) * 1000)

        # METADATA ONLY. No prompt, no completion text — see the module docstring.
        _log.info(json.dumps({
            "event": "completion",
            "tenant": tenant,
            "provider": completion.provider,
            "model": completion.model,
            "input_tokens": completion.input_tokens,
            "output_tokens": completion.output_tokens,
            "elapsed_ms": elapsed_ms,
        }))

        return {
            "text": completion.text,
            "usage": {
                "input_tokens": completion.input_tokens,
                "output_tokens": completion.output_tokens,
                "total_tokens": completion.total_tokens,
            },
            "provider": completion.provider,
            "model": completion.model,
        }


class Handler(BaseHTTPRequestHandler):
    """Two endpoints. A gateway with more surface than it needs is a gateway
    with more surface to get wrong."""

    gateway: Gateway = None          # injected by serve()
    server_version = "nc-ai-gateway"
    sys_version = ""                 # do not advertise the Python version

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _drain(self, remaining: int) -> None:
        """Read and discard a bounded number of body bytes.

        Never accumulates: the whole point is to let the client finish writing
        so the response is delivered cleanly, not to buffer what was sent.
        """
        while remaining > 0:
            chunk = self.rfile.read(min(8192, remaining))
            if not chunk:
                return
            remaining -= len(chunk)

    def log_message(self, fmt, *args):  # noqa: D102
        # Silence BaseHTTPRequestHandler's stderr access log: it would print the
        # request line, and this process must not emit anything that could carry
        # tenant content. Real events are logged structurally above.
        return

    def do_GET(self):  # noqa: N802
        if self.path == "/healthz":
            self._send(200, {
                "status": "ok",
                "provider": self.gateway.provider.name,
                "circuit": self.gateway.breaker.state,
            })
            return
        self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if self.path != "/v1/complete":
            self._send(404, {"error": "not found"})
            return

        try:
            length = int(self.headers.get("content-length") or 0)
        except ValueError:
            self._send(400, {"error": "invalid content-length"})
            return
        if length <= 0 or length > MAX_REQUEST_BYTES:
            # DRAIN BEFORE REFUSING. Answering while the client is still writing
            # makes the kernel reset the connection, and the caller sees an
            # opaque "connection reset by peer" instead of the 413 that tells
            # them what is wrong. Caught by test_a_huge_body_is_refused.
            #
            # Draining is bounded: at most one MAX_REQUEST_BYTES beyond the
            # limit, so an absurd content-length still cannot make this process
            # read forever.
            self._drain(min(length, MAX_REQUEST_BYTES * 2))
            self._send(413, {"error": "request body must be 1..%d bytes"
                                      % MAX_REQUEST_BYTES})
            return

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            tenant = str(payload["tenant"])
            prompt = str(payload["prompt"])
            max_tokens = int(payload.get("max_tokens", DEFAULT_MAX_TOKENS))
        except (ValueError, KeyError, TypeError):
            # No echo of what was sent — the body is tenant content.
            self._send(400, {"error": "body must be JSON with 'tenant' and 'prompt'"})
            return
        if not tenant or not prompt:
            self._send(400, {"error": "'tenant' and 'prompt' must be non-empty"})
            return

        try:
            self._send(200, self.gateway.complete(tenant, prompt, max_tokens))
        except BudgetExceeded as exc:
            # THE ACCEPTANCE CRITERION: "budget exhaustion returns a friendly
            # error". 429 with an explanation and a retry hint — never a 500,
            # which would read as a platform fault and invite a retry storm.
            self._send(429, {
                "error": "ai_budget_exhausted",
                "message": ("This workspace has used its AI allowance for now. "
                            "It refreshes automatically — try again later, or "
                            "contact your administrator to raise the limit."),
                "detail": str(exc),
                "usage": self.gateway.ledger.usage(tenant),
            })
        except RateLimited as exc:
            self._send(429, {
                "error": "ai_rate_limited",
                "message": ("Too many AI requests in a short time. "
                            "Please wait a moment and try again."),
                "detail": str(exc),
            })
        except CircuitOpen as exc:
            self._send(503, {
                "error": "ai_provider_unavailable",
                "message": ("The AI provider is temporarily unavailable. "
                            "Your data was not sent anywhere."),
                "detail": str(exc),
            })
        except ProviderError as exc:
            # str(exc) is a status code or a reason string by construction —
            # providers.py never puts a response body into the message.
            _log.warning(json.dumps({"event": "provider_error",
                                     "tenant": tenant, "detail": str(exc)}))
            self._send(502, {"error": "ai_provider_error",
                             "message": "The AI provider returned an error.",
                             "detail": str(exc)})


def serve(host: str = "0.0.0.0", port: int = 8080) -> None:  # noqa: S104
    Handler.gateway = Gateway()
    _log.info(json.dumps({
        "event": "startup", "provider": Handler.gateway.provider.name,
        "port": port,
    }))
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    serve(port=int(os.environ.get("NC_AI_PORT", 8080)))
