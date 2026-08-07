# -*- coding: utf-8 -*-
"""LLM provider abstraction for the AI gateway satellite (P5-T02 / #59).

WHY THE ABSTRACTION COMES FIRST
-------------------------------
AI_PLATFORM_DESIGN.md §2.3 is explicit that the provider choice is deliberately
*not* load-bearing: "what is load-bearing is that the abstraction is written
before a provider is chosen, so the first provider's quirks never leak into
callers." Data residency (P10-T05, five phases away) will eventually force a
region swap, and that must stay a config change rather than a rewrite.

So callers see one shape — ``complete(prompt, max_tokens) -> Completion`` — and
providers are selected by name from configuration.

STDLIB ONLY, ON PURPOSE
-----------------------
No `requests`, no SDK, no framework. CLAUDE.md Rule 3 forbids new architectural
dependencies without approval, and a satellite whose whole job is one HTTPS POST
does not need any. It also keeps the container small and its supply chain
auditable, which matters more here than convenience: this process holds the LLM
API keys.

THE OUTBOUND CALL REUSES THE SHIPPED HARDENING
----------------------------------------------
ARCHITECTURE_SECURITY §11 requires this egress to reuse config_sync.py's
bounded-read / deadline / no-redirect patterns (#278/#283/#308/#309) rather than
invent its own. The three that matter, and why:

* **No redirects.** "One allowlisted host" is worth nothing if the client follows
  a 302 to somewhere nobody allowlisted (exchange_rate.py:140).
* **Total deadline, not just a socket timeout.** A per-read timeout resets on
  every byte, so a trickling response can hold a connection open indefinitely.
* **Bounded read.** A hostile or broken response must not be able to exhaust
  memory.
"""
from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

# Mirrors config_sync.py's constants rather than inventing new numbers, so the
# two egress paths behave identically under stress and one runbook covers both.
RPC_TIMEOUT = 30            # idle/socket bound (seconds)
RPC_DEADLINE = 60           # TOTAL duration budget (seconds)
MAX_RESPONSE_BYTES = 64 * 1024


class ProviderError(RuntimeError):
    """Any provider-side failure. Carries no prompt or completion content."""


@dataclass(frozen=True)
class Completion:
    """What every provider returns, whatever its wire format."""
    text: str
    input_tokens: int
    output_tokens: int
    provider: str
    model: str

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class Provider:
    """Base class. A provider translates one wire format; nothing more.

    Budgets, rate limiting, circuit breaking and logging live in the gateway,
    not here — otherwise every new provider would have to re-implement the
    controls, and one of them would eventually get it wrong.
    """

    name = "base"
    #: The single allowlisted host this provider may reach. Enforced here AND at
    #: the network layer by #311's DOCKER-USER backstop — a code-level
    #: allowlist alone was explicitly judged insufficient (§11).
    host = ""

    def complete(self, prompt: str, max_tokens: int) -> Completion:
        raise NotImplementedError


class MockProvider(Provider):
    """The default. Deterministic, offline, and reaches nothing.

    This is what ships enabled. A real provider needs an API key that this
    repository does not have and must never contain, so the gateway is built and
    tested against this one; swapping to Anthropic is a config change
    (NC_AI_PROVIDER=anthropic + NC_AI_API_KEY).

    It is NOT a stub that pretends to succeed: it reports token counts derived
    from the real input so budget accounting, rate limiting and the circuit
    breaker are all exercised honestly in tests.
    """

    name = "mock"
    host = ""  # deliberately unreachable — no egress at all

    def complete(self, prompt: str, max_tokens: int) -> Completion:
        # ~4 chars/token is the usual rough ratio; exact enough for budget tests
        # and honest about being an estimate.
        input_tokens = max(1, len(prompt) // 4)
        text = "[mock completion]"
        return Completion(
            text=text,
            input_tokens=input_tokens,
            output_tokens=min(max_tokens, max(1, len(text) // 4)),
            provider=self.name,
            model="mock-1",
        )


class AnthropicProvider(Provider):
    """Claude via the Messages API — the recommended default (§2.3).

    Recommended, not committed: §2.3 calls the choice "a preference, not a
    commitment", which is precisely why it sits behind this interface.
    """

    name = "anthropic"
    host = "api.anthropic.com"
    _URL = "https://api.anthropic.com/v1/messages"
    _VERSION = "2023-06-01"

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-5") -> None:
        if not api_key:
            # Fail at construction, not mid-request: a gateway that starts
            # without credentials and only discovers it under load has turned a
            # config error into an outage.
            raise ProviderError("anthropic provider selected but NC_AI_API_KEY is empty")
        self._api_key = api_key
        self._model = model

    def complete(self, prompt: str, max_tokens: int) -> Completion:
        body = json.dumps({
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }).encode("utf-8")

        request = urllib.request.Request(
            self._URL,
            data=body,
            method="POST",
            headers={
                "content-type": "application/json",
                "anthropic-version": self._VERSION,
                "x-api-key": self._api_key,
            },
        )

        # A default-verifying context, stated explicitly rather than relied upon:
        # this connection carries tenant-derived content to a third party.
        ctx = ssl.create_default_context()

        started = time.monotonic()
        try:
            # NO redirect handling: urllib's default opener follows redirects,
            # which would let a 302 walk this request off the one allowlisted
            # host. exchange_rate.py:140 documents the same reasoning.
            opener = urllib.request.build_opener(_NoRedirect())
            with opener.open(request, timeout=RPC_TIMEOUT, context=ctx) as response:
                buf = b""
                while True:
                    if time.monotonic() - started > RPC_DEADLINE:
                        raise ProviderError(
                            "provider exceeded the %ss total deadline" % RPC_DEADLINE)
                    chunk = response.read1(8192)
                    if not chunk:
                        break
                    buf += chunk
                    if len(buf) > MAX_RESPONSE_BYTES:
                        raise ProviderError(
                            "provider response exceeded %s bytes" % MAX_RESPONSE_BYTES)
        except urllib.error.HTTPError as exc:
            # Status only. The body may echo the prompt back, and this process
            # must never log tenant content (§11: "metadata only, never bodies").
            raise ProviderError("provider returned HTTP %s" % exc.code) from None
        except urllib.error.URLError as exc:
            raise ProviderError("provider unreachable: %s" % exc.reason) from None

        try:
            payload = json.loads(buf.decode("utf-8"))
            text = "".join(
                block.get("text", "") for block in payload.get("content", []))
            usage = payload.get("usage", {})
            return Completion(
                text=text,
                input_tokens=int(usage.get("input_tokens", 0)),
                output_tokens=int(usage.get("output_tokens", 0)),
                provider=self.name,
                model=payload.get("model", self._model),
            )
        except (ValueError, KeyError, TypeError):
            raise ProviderError("provider returned an unparseable response") from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Turn any redirect into a hard error instead of following it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        raise ProviderError("provider attempted a redirect to another host")


def build_provider(env: dict | None = None) -> Provider:
    """Select the provider from configuration. Defaults to `mock`.

    Defaulting to mock is a safety property, not laziness: a misconfigured
    deployment produces obviously-fake completions rather than silently
    spending money or, worse, shipping tenant content somewhere unintended.
    """
    env = os.environ if env is None else env
    name = (env.get("NC_AI_PROVIDER") or "mock").strip().lower()
    if name == "mock":
        return MockProvider()
    if name == "anthropic":
        return AnthropicProvider(
            api_key=env.get("NC_AI_API_KEY", ""),
            model=env.get("NC_AI_MODEL", "claude-sonnet-4-5"),
        )
    raise ProviderError("unknown NC_AI_PROVIDER %r (known: mock, anthropic)" % name)
