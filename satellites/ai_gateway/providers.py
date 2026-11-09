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

    def stream(self, prompt: str, max_tokens: int):
        """Yield text chunks (str), then ONE final `Completion` carrying the usage.

        The default is honest rather than clever: a provider with no streaming
        wire format yields its whole answer as a single chunk, so the stream
        endpoint works against every provider and only the ones that can
        actually stream get the latency benefit. The final item is always a
        `Completion`; a consumer that never sees one must treat the stream as
        failed (a connection cut mid-answer is not a short answer).
        """
        completion = self.complete(prompt, max_tokens)
        if completion.text:
            yield completion.text
        yield completion




# ---------------------------------------------------------------------------
# The mock's grounded answer
# ---------------------------------------------------------------------------
# The mock reaches nothing and understands nothing, but it can still be USEFUL in a demonstration: when the prompt carries
# workspace data (the labelled, pseudonymised sections the tenant built), it states the headline figures of those sections
# in plain words. That is arithmetic over the prompt, not "AI", and the text says so. A real provider replaces all of it.
_DATA_MARK_START = "--- WORKSPACE DATA ---\n"
_DATA_MARK_END = "\n--- END DATA ---"
# section key -> (value column, English label, Arabic label)
_SECTIONS = {
    "receivables": ("amount_residual:sum", "Open customer invoices", "فواتير العملاء المفتوحة"),
    "overdue_receivables": ("amount_residual:sum", "Overdue customer invoices", "فواتير العملاء المتأخرة"),
    "payables": ("amount_residual:sum", "Open vendor bills", "فواتير الموردين المفتوحة"),
    "recent_invoices": ("amount_total:sum", "Invoiced in the last months", "المفوتر في الأشهر الأخيرة"),
    "sales_by_month": ("amount_total:sum", "Confirmed sales orders", "أوامر البيع المؤكدة"),
    "top_customers": ("amount_total:sum", "Top customers by orders", "أفضل العملاء حسب الطلبات"),
    "purchases_by_supplier": ("amount_total:sum", "Confirmed purchases", "المشتريات المؤكدة"),
    "customer_payments_by_month": ("amount:sum", "Customer payments received", "المدفوعات المستلمة من العملاء"),
    "stock_on_hand": ("quantity:sum", "Units on hand (top products)", "الوحدات في المخزون (أعلى المنتجات)"),
}


def grounded_summary(prompt: str) -> str:
    """Headline figures of the data sections found in ``prompt`` ('' when there are none)."""
    start = prompt.find(_DATA_MARK_START)
    end = prompt.find(_DATA_MARK_END, start + 1)
    if start < 0 or end < 0:
        return ""
    try:
        data = json.loads(prompt[start + len(_DATA_MARK_START):end])
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    arabic = "answer in Arabic" in prompt
    workspace = data.get("workspace") if isinstance(data.get("workspace"), dict) else {}
    currency = str(workspace.get("currency") or "")
    lines = []
    for key, (column, english, arabic_label) in _SECTIONS.items():
        rows = data.get(key)
        if not isinstance(rows, list) or not rows:
            continue
        values = [r.get(column) for r in rows if isinstance(r, dict) and isinstance(r.get(column), (int, float))]
        if not values:
            continue
        total = sum(values)
        figure = "{:,.0f}".format(total) if key == "stock_on_hand" else "{:,.2f} {}".format(total, currency).strip()
        top = next((str(v) for r in rows if isinstance(r, dict)
                    for k, v in r.items() if k in ("partner_id", "product_id") and v), "")
        label = arabic_label if arabic else english
        lines.append("%s: %s%s" % (label, figure, (" (%s %s)" % ("الأعلى" if arabic else "top", top)) if top else ""))
    if not lines:
        return ""
    head = ("إجابة تجريبية من بياناتك (لا يوجد مزوّد ذكاء اصطناعي متصل): " if arabic
            else "Demo answer from your workspace data (no AI provider is connected): ")
    return head + "; ".join(lines) + "."
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
        text = "[mock completion]" + ((" " + grounded_summary(prompt)) if grounded_summary(prompt) else "")
        return Completion(
            text=text,
            input_tokens=input_tokens,
            output_tokens=min(max_tokens, max(1, len(text) // 4)),
            provider=self.name,
            model="mock-1",
        )

    def stream(self, prompt: str, max_tokens: int):
        # Word-sized chunks of a longer deterministic text, so the stream path
        # is exercised for real: more than one chunk, a placeholder-looking
        # token that can straddle a chunk boundary, and the usage at the end.
        completion = self.complete(prompt, max_tokens)
        words = ("[mock completion] This answer is streamed in several small "
                 "pieces so the widget can show it as it arrives." + (" " + grounded_summary(prompt) if grounded_summary(prompt) else "")).split(" ")
        text = ""
        # NC_AI_MOCK_STREAM_DELAY_MS slows the mock so a browser test can SEE
        # the answer grow; 0 (the default) keeps every unit test instant.
        delay = float(os.environ.get("NC_AI_MOCK_STREAM_DELAY_MS", "0") or 0) / 1000.0
        for index, word in enumerate(words):
            piece = word if index == 0 else " " + word
            text += piece
            yield piece
            if delay:
                time.sleep(delay)
        yield Completion(
            text=text,
            input_tokens=completion.input_tokens,
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


    def stream(self, prompt: str, max_tokens: int):
        body = json.dumps({
            "model": self._model,
            "max_tokens": max_tokens,
            "stream": True,
            "messages": [{"role": "user", "content": prompt}],
        }).encode("utf-8")
        request = urllib.request.Request(
            self._URL, data=body, method="POST",
            headers={"content-type": "application/json",
                     "anthropic-version": self._VERSION,
                     "x-api-key": self._api_key})
        ctx = ssl.create_default_context()
        started = time.monotonic()
        total = 0
        text = ""
        try:
            opener = urllib.request.build_opener(_NoRedirect())
            with opener.open(request, timeout=RPC_TIMEOUT, context=ctx) as response:
                def lines():
                    nonlocal total
                    for raw in response:
                        if time.monotonic() - started > RPC_DEADLINE:
                            raise ProviderError(
                                "provider exceeded the %ss total deadline" % RPC_DEADLINE)
                        total += len(raw)
                        if total > MAX_RESPONSE_BYTES:
                            raise ProviderError(
                                "provider response exceeded %s bytes" % MAX_RESPONSE_BYTES)
                        yield raw.decode("utf-8", "replace")
                final = None
                for item in parse_anthropic_stream(lines()):
                    if isinstance(item, dict):
                        final = item
                    else:
                        text += item
                        yield item
        except urllib.error.HTTPError as exc:
            raise ProviderError("provider returned HTTP %s" % exc.code) from None
        except urllib.error.URLError as exc:
            raise ProviderError("provider unreachable: %s" % exc.reason) from None
        if final is None:
            raise ProviderError("provider stream ended without a final frame")
        yield Completion(text=text, input_tokens=final["usage"]["input_tokens"],
                         output_tokens=final["usage"]["output_tokens"],
                         provider=self.name, model=final["model"] or self._model)


def parse_anthropic_stream(lines):
    """Yield text chunks, then the usage dict, from an Anthropic SSE stream.

    Pure (it takes an iterable of decoded lines), so it is unit-tested without a
    network. Only the three events that matter are read: the input token count
    (message_start), each text delta (content_block_delta), and the output
    token count (message_delta). Everything else - ping, block start/stop - is
    ignored. Raises ProviderError for an error event or an unparseable frame.
    """
    usage = {"input_tokens": 0, "output_tokens": 0}
    model = ""
    for line in lines:
        line = line.strip()
        if not line.startswith("data:"):
            continue
        try:
            event = json.loads(line[5:].strip())
        except ValueError:
            raise ProviderError("provider sent an unparseable stream frame") from None
        kind = event.get("type")
        if kind == "message_start":
            message = event.get("message", {})
            model = message.get("model", model)
            usage["input_tokens"] = int(message.get("usage", {}).get("input_tokens", 0))
        elif kind == "content_block_delta":
            delta = event.get("delta", {})
            if delta.get("type") == "text_delta" and delta.get("text"):
                yield delta["text"]
        elif kind == "message_delta":
            usage["output_tokens"] = int(event.get("usage", {}).get("output_tokens", 0))
        elif kind == "error":
            # The error type only - the message may echo content.
            raise ProviderError("provider stream error: %s"
                                % str(event.get("error", {}).get("type", "unknown"))[:40])
    yield {"usage": usage, "model": model}


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
