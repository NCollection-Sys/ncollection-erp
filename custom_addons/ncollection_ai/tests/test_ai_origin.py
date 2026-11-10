# -*- coding: utf-8 -*-
"""An assistant answer says WHO produced it: a configured model, or the demonstration (mock) provider."""

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAnswerOrigin(TransactionCase):
    def test_origin_is_saved_and_returned_with_the_answer_and_the_history(self):
        chat = self.env['ncollection.ai.chat']
        demo = chat._persist_exchange(
            'How much do customers owe?', {'answer': '[mock completion] x', 'origin': 'demo', 'model_name': 'mock-1'}
        )
        live = chat._persist_exchange(
            'And suppliers?', {'answer': 'Real answer', 'origin': 'model', 'model_name': 'claude-test'}
        )
        answers = [m for r in (demo, live) for m in r['messages'] if m['role'] == 'assistant']
        self.assertEqual(
            [(a['origin'], a['model_name']) for a in answers], [('demo', 'mock-1'), ('model', 'claude-test')]
        )
        users = [m for r in (demo, live) for m in r['messages'] if m['role'] == 'user']
        self.assertTrue(all(not u['origin'] for u in users))
        history = {
            h['id']: h
            for h in self.env['ncollection.ai.chat.message'].search_read(
                [('user_id', '=', self.env.uid)], ['origin', 'model_name']
            )
        }
        self.assertEqual(history[answers[0]['id']]['origin'], 'demo')

    def test_the_mock_provider_is_never_labelled_as_a_model(self):
        # the stream controller maps the gateway's provider name; the mapping lives in one place and is explicit
        import inspect
        from odoo.addons.ncollection_ai.controllers import chat_stream

        source = inspect.getsource(chat_stream)
        self.assertIn("'demo' if provider == 'mock' else 'model'", source)

    def test_a_row_without_origin_stays_valid(self):
        row = (
            self.env['ncollection.ai.chat.message']
            .sudo()
            .create({'user_id': self.env.uid, 'role': 'assistant', 'body': 'old row'})
        )
        self.assertFalse(row.origin)
