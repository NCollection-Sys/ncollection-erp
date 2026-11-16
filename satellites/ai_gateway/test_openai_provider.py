# -*- coding: utf-8 -*-
"""OpenAI adapter: parsing and selection, with NO network (stdlib unittest, like the other gateway tests)."""
import unittest

from provider_openai import OpenAIProvider, parse_openai_completion, parse_openai_stream
from providers import ProviderError, build_provider


class TestOpenAIAdapter(unittest.TestCase):

    def test_selected_by_configuration_and_refuses_without_a_key(self):
        provider = build_provider({"NC_AI_PROVIDER": "openai", "NC_AI_API_KEY": "sk-test"})
        self.assertIsInstance(provider, OpenAIProvider)
        self.assertEqual(provider.host, "api.openai.com")
        self.assertEqual(build_provider({"NC_AI_PROVIDER": "openai", "NC_AI_API_KEY": "k",
                                         "NC_AI_MODEL": "gpt-x"})._model, "gpt-x")
        with self.assertRaises(ProviderError):
            build_provider({"NC_AI_PROVIDER": "openai", "NC_AI_API_KEY": ""})
        self.assertEqual(build_provider({}).name, "mock", "the default still reaches nothing")

    def test_the_key_travels_only_in_the_header(self):
        request = OpenAIProvider("sk-secret")._request("hello", 50, True)
        self.assertEqual(request.full_url, "https://api.openai.com/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer sk-secret")
        self.assertNotIn(b"sk-secret", request.data)
        self.assertIn(b'"include_usage": true', request.data)

    def test_completion_body(self):
        body = (b'{"model": "gpt-4.1-mini-2025", "choices": [{"message": {"content": "Revenue rose 4%."}}],'
                b' "usage": {"prompt_tokens": 120, "completion_tokens": 9}}')
        result = parse_openai_completion(body, "gpt-4.1-mini")
        self.assertEqual((result.text, result.input_tokens, result.output_tokens, result.model),
                         ("Revenue rose 4%.", 120, 9, "gpt-4.1-mini-2025"))
        with self.assertRaises(ProviderError):
            parse_openai_completion(b'{"choices": []}', "m")

    def test_stream_yields_text_then_usage(self):
        frames = ['data: {"model": "m1", "choices": [{"delta": {"role": "assistant"}}]}',
                  'data: {"model": "m1", "choices": [{"delta": {"content": "Hel"}}]}', '',
                  'data: {"model": "m1", "choices": [{"delta": {"content": "lo"}}]}',
                  'data: {"model": "m1", "choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2}}',
                  'data: [DONE]']
        items = list(parse_openai_stream(frames))
        self.assertEqual(items[:2], ["Hel", "lo"])
        self.assertEqual(items[-1], {"usage": {"input_tokens": 5, "output_tokens": 2}, "model": "m1"})

    def test_stream_error_is_a_type_never_a_body(self):
        with self.assertRaises(ProviderError) as caught:
            list(parse_openai_stream(['data: {"error": {"type": "insufficient_quota", "message": "secret prompt"}}']))
        self.assertNotIn("secret prompt", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
