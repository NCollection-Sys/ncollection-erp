# -*- coding: utf-8 -*-
"""OpenAI via the Chat Completions API (owner decision 2026-11-16: the first preferred real provider).

Same hardening as the Anthropic adapter in providers.py, reused rather than re-invented: stdlib only, no redirects,
a total deadline on top of the socket timeout, a bounded read, status-only errors (a provider body may echo the
prompt, and this process never logs tenant content). Selected by configuration only (NC_AI_PROVIDER=openai +
NC_AI_API_KEY + optional NC_AI_MODEL); the model id is configuration, never code.
"""
from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request

from providers import (MAX_RESPONSE_BYTES, RPC_DEADLINE, RPC_TIMEOUT, Completion, Provider, ProviderError,
                       _NoRedirect)


class OpenAIProvider(Provider):
    name = "openai"
    host = "api.openai.com"
    _URL = "https://api.openai.com/v1/chat/completions"
    DEFAULT_MODEL = "gpt-4.1-mini"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL) -> None:
        if not api_key:
            raise ProviderError("openai provider selected but NC_AI_API_KEY is empty")
        self._api_key = api_key
        self._model = model or self.DEFAULT_MODEL

    def _request(self, prompt: str, max_tokens: int, stream: bool) -> urllib.request.Request:
        payload = {
            "model": self._model,
            "max_completion_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        return urllib.request.Request(
            self._URL, data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"content-type": "application/json", "authorization": "Bearer %s" % self._api_key})

    def complete(self, prompt: str, max_tokens: int) -> Completion:
        ctx = ssl.create_default_context()
        started = time.monotonic()
        try:
            opener = urllib.request.build_opener(_NoRedirect())
            with opener.open(self._request(prompt, max_tokens, False), timeout=RPC_TIMEOUT, context=ctx) as resp:
                buf = b""
                while True:
                    if time.monotonic() - started > RPC_DEADLINE:
                        raise ProviderError("provider exceeded the %ss total deadline" % RPC_DEADLINE)
                    chunk = resp.read1(8192)
                    if not chunk:
                        break
                    buf += chunk
                    if len(buf) > MAX_RESPONSE_BYTES:
                        raise ProviderError("provider response exceeded %s bytes" % MAX_RESPONSE_BYTES)
        except urllib.error.HTTPError as exc:
            raise ProviderError("provider returned HTTP %s" % exc.code) from None
        except urllib.error.URLError as exc:
            raise ProviderError("provider unreachable: %s" % exc.reason) from None
        return parse_openai_completion(buf, self._model, self.name)

    def stream(self, prompt: str, max_tokens: int):
        ctx = ssl.create_default_context()
        started = time.monotonic()
        total = 0
        text = ""
        try:
            opener = urllib.request.build_opener(_NoRedirect())
            with opener.open(self._request(prompt, max_tokens, True), timeout=RPC_TIMEOUT, context=ctx) as resp:
                def lines():
                    nonlocal total
                    for raw in resp:
                        if time.monotonic() - started > RPC_DEADLINE:
                            raise ProviderError("provider exceeded the %ss total deadline" % RPC_DEADLINE)
                        total += len(raw)
                        if total > MAX_RESPONSE_BYTES:
                            raise ProviderError("provider response exceeded %s bytes" % MAX_RESPONSE_BYTES)
                        yield raw.decode("utf-8", "replace")
                final = None
                for item in parse_openai_stream(lines()):
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
            raise ProviderError("provider stream ended without usage (stream_options.include_usage)")
        yield Completion(text=text, input_tokens=final["usage"]["input_tokens"],
                         output_tokens=final["usage"]["output_tokens"],
                         provider=self.name, model=final["model"] or self._model)


def parse_openai_completion(buf: bytes, model: str, name: str = "openai") -> Completion:
    """Pure: a Chat Completions JSON body -> Completion. ProviderError on anything unexpected."""
    try:
        payload = json.loads(buf.decode("utf-8"))
        choice = payload["choices"][0]
        text = (choice.get("message") or {}).get("content") or ""
        usage = payload.get("usage") or {}
        return Completion(text=text, input_tokens=int(usage.get("prompt_tokens", 0)),
                          output_tokens=int(usage.get("completion_tokens", 0)),
                          provider=name, model=payload.get("model", model))
    except (ValueError, KeyError, TypeError, IndexError):
        raise ProviderError("provider returned an unparseable response") from None


def parse_openai_stream(lines):
    """Yield text chunks, then {usage, model}, from a Chat Completions SSE stream (``data: {...}`` frames, a final
    usage-only chunk when ``include_usage`` is set, then ``data: [DONE]``). Pure: unit-tested without a network."""
    usage = None
    model = ""
    for line in lines:
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            event = json.loads(data)
        except ValueError:
            raise ProviderError("provider sent an unparseable stream frame") from None
        if "error" in event:
            raise ProviderError("provider stream error: %s"
                                % str((event.get("error") or {}).get("type", "unknown"))[:40])
        model = event.get("model", model)
        for choice in event.get("choices") or []:
            content = (choice.get("delta") or {}).get("content")
            if content:
                yield content
        if event.get("usage"):
            usage = {"input_tokens": int(event["usage"].get("prompt_tokens", 0)),
                     "output_tokens": int(event["usage"].get("completion_tokens", 0))}
    if usage is not None:
        yield {"usage": usage, "model": model}
