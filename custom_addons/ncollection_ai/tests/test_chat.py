# -*- coding: utf-8 -*-
"""Tests for the chat widget's server half (P5-T06 / #63).

The widget itself is presentation; every claim that matters is a SERVER claim,
so every proof lives here:

  * the financial gate holds on every RPC path, with a passing control so
    "refuses everyone" cannot masquerade as security;
  * a curated prompt works while the #375 free-text flag is OFF, because its
    text is server-owned — and the prompt that crosses the wire is asserted to
    BE the registry text, not whatever the client sent;
  * typed input is a thin wrapper over ask() and inherits its refusals;
  * history is private per user, proven in both directions (search filtering
    AND direct-id read), each with a control on a non-empty fixture — the #66
    house style, because an isolation test that passes on an empty fixture
    proves nothing;
  * a refused or failed request persists nothing — a transcript showing an
    answer that was never given is worse than no transcript.

The gateway is intercepted exactly the way test_ai_question.py does it: these
tests assert on what WOULD cross the boundary, with no network anywhere.
"""
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

_GATEWAY_TARGET = ('odoo.addons.ncollection_ai.models.gateway_client'
                   '.AiGatewayClient._complete')


@tagged('post_install', '-at_install')
class TestChatService(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Chat = cls.env['ncollection.ai.chat']
        cls.Message = cls.env['ncollection.ai.chat.message']
        base_user = cls.env.ref('base.group_user')
        accountant = cls.env.ref('ncollection_core.group_role_accountant')

        def user(login, groups):
            return cls.env['res.users'].create({
                'name': login, 'login': login,
                'group_ids': [(6, 0, [g.id for g in groups])],
            })

        cls.acct_a = user('chat_acct_a', [base_user, accountant])
        cls.acct_b = user('chat_acct_b', [base_user, accountant])
        cls.sales = user('chat_sales', [
            base_user, cls.env.ref('ncollection_core.group_role_sales')])

    def _with_gateway(self, answer='ok'):
        """Patch the gateway; returns a dict capturing calls and prompts."""
        captured = {'calls': 0, 'prompts': []}

        def fake_complete(_self, prompt, max_tokens=1024):
            captured['calls'] += 1
            captured['prompts'].append(prompt)
            return {'text': answer, 'usage': {}}

        patcher = patch(_GATEWAY_TARGET, new=fake_complete)
        patcher.start()
        self.addCleanup(patcher.stop)
        return captured

    # -------------------------------------------------------------- get_state
    def test_get_state_reports_disabled_rather_than_raising(self):
        """The widget mounts for EVERY backend session. A non-finance user
        must get a quiet 'not for you', not a traceback in the console for
        loading the web client."""
        state = self.Chat.with_user(self.sales).get_state()
        self.assertEqual(state, {'enabled': False})

    def test_get_state_for_an_accountant_carries_the_ui_contract(self):
        state = self.Chat.with_user(self.acct_a).get_state()
        self.assertTrue(state['enabled'])
        # Free text is OFF on a fresh workspace — the widget must learn that
        # from the server, not assume it.
        self.assertFalse(state['free_text'])
        self.assertTrue(state['suggestions'])
        # Labels only: the canonical question text stays server-side.
        for chip in state['suggestions']:
            self.assertEqual(sorted(chip), ['key', 'label'])

    def test_suggestions_follow_the_module_context_with_a_default_fallback(self):
        as_a = self.Chat.with_user(self.acct_a)
        sale_keys = [c['key'] for c in
                     as_a.get_state(module_ctx='sale.order')['suggestions']]
        default_keys = [c['key'] for c in as_a.get_state()['suggestions']]
        self.assertIn('top_customers', sale_keys)
        self.assertNotEqual(sale_keys, default_keys)
        # Unknown and even non-string contexts must fall back, not crash —
        # module_ctx arrives from the client and is untrusted like all input.
        self.assertEqual(
            [c['key'] for c in
             as_a.get_state(module_ctx='no.such.model')['suggestions']],
            default_keys)
        self.assertEqual(
            [c['key'] for c in
             as_a.get_state(module_ctx={'evil': 1})['suggestions']],
            default_keys)

    def test_the_registry_is_internally_consistent(self):
        """Every key a module list references must exist in prompts — a chip
        that 404s on click is a data bug this test turns into a red build."""
        from ..models.chat import _prompt_catalog
        catalog = _prompt_catalog()
        for module, keys in catalog['modules'].items():
            for key in keys:
                self.assertIn(key, catalog['prompts'],
                              '%s references unknown key %s' % (module, key))
        self.assertIn('default', catalog['modules'])

    # -------------------------------------------------------- suggested path
    def test_a_suggested_prompt_works_while_free_text_is_OFF(self):
        """THE POINT OF THE TICKET'S SERVER DESIGN. #375 keeps arbitrary user
        prose away from the provider; a server-owned prompt is not that, so
        the widget is usable on a fresh workspace with the flag untouched."""
        self.assertFalse(
            self.env['ncollection.ai.question']._free_text_enabled())
        captured = self._with_gateway(answer='Here is your summary.')

        result = self.Chat.with_user(self.acct_a).send_suggested(
            'workspace_summary')

        self.assertEqual(captured['calls'], 1)
        self.assertEqual(result['messages'][0]['role'], 'user')
        self.assertEqual(result['messages'][1]['role'], 'assistant')
        self.assertEqual(result['messages'][1]['body'],
                         'Here is your summary.')

    def test_the_prompt_sent_is_the_registry_text_not_client_text(self):
        """The client transmits a KEY. The question that crosses the wire must
        be the registry's canonical sentence — that equality is what makes
        the #375 argument true rather than asserted."""
        captured = self._with_gateway()
        self.Chat.with_user(self.acct_a).send_suggested('top_debtor')
        self.assertIn('Which customer owes us the most right now?',
                      captured['prompts'][0])

    def test_an_unknown_key_is_refused_and_nothing_is_sent_or_stored(self):
        captured = self._with_gateway()
        for bad in ('no_such_key',
                    'Which customer owes us the most right now?',  # text != key
                    {'key': 'workspace_summary'}, None, 42):
            with self.assertRaises(UserError, msg=repr(bad)):
                self.Chat.with_user(self.acct_a).send_suggested(bad)
        self.assertEqual(captured['calls'], 0)
        self.assertFalse(self.Message.with_user(self.acct_a).search([]))

    def test_the_gate_holds_on_the_suggested_path_with_a_passing_control(self):
        """Standing Rule 4 both ways: the sales user is refused at the RPC
        layer, and the accountant control proves the gate admits the intended
        roles — refusing everyone would pass the first half alone."""
        captured = self._with_gateway()
        with self.assertRaises(AccessError):
            self.Chat.with_user(self.sales).send_suggested('workspace_summary')
        self.assertEqual(captured['calls'], 0)
        self.Chat.with_user(self.acct_a).send_suggested('workspace_summary')
        self.assertEqual(captured['calls'], 1)

    # ------------------------------------------------------------ typed path
    def test_typed_input_is_refused_while_the_flag_is_off_and_leaves_no_trace(self):
        """send_typed is a wrapper over ask(), so #375's default refusal
        applies unchanged — and the refusal must not fabricate history."""
        captured = self._with_gateway()
        with self.assertRaises(UserError) as caught:
            self.Chat.with_user(self.acct_a).send_typed(
                'Which customer owes us the most?')
        self.assertIn('not enabled', str(caught.exception))
        self.assertEqual(captured['calls'], 0)
        self.assertFalse(self.Message.with_user(self.acct_a).search([]))

    def test_typed_input_works_only_when_the_flag_is_deliberately_on(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'ncollection_ai.enable_free_text_questions', 'True')
        self.addCleanup(
            self.env['ir.config_parameter'].sudo().set_param,
            'ncollection_ai.enable_free_text_questions', 'False')
        captured = self._with_gateway(answer='42 invoices.')

        result = self.Chat.with_user(self.acct_a).send_typed(
            'How many customers have unpaid invoices?')

        self.assertEqual(captured['calls'], 1)
        self.assertEqual(result['messages'][1]['body'], '42 invoices.')
        stored = self.Message.with_user(self.acct_a).search([], order='id')
        self.assertEqual(stored.mapped('role'), ['user', 'assistant'])

    def test_typed_input_still_passes_through_asks_secret_scan(self):
        """The wrapper must not open a path around ask()'s gauntlet: a pasted
        credential is refused by the same scan, and nothing is stored."""
        self.env['ir.config_parameter'].sudo().set_param(
            'ncollection_ai.enable_free_text_questions', 'True')
        self.addCleanup(
            self.env['ir.config_parameter'].sudo().set_param,
            'ncollection_ai.enable_free_text_questions', 'False')
        captured = self._with_gateway()
        with self.assertRaises(UserError) as caught:
            self.Chat.with_user(self.acct_a).send_typed(
                'root password is hunter2 for the staging box')
        self.assertIn('Nothing was sent', str(caught.exception))
        self.assertEqual(captured['calls'], 0)
        self.assertFalse(self.Message.with_user(self.acct_a).search([]))

    # --------------------------------------------------------------- history
    def test_history_is_persisted_in_order_and_returned_by_get_state(self):
        self._with_gateway(answer='Answer one.')
        as_a = self.Chat.with_user(self.acct_a)
        as_a.send_suggested('workspace_summary')
        history = as_a.get_state()['history']
        self.assertEqual([h['role'] for h in history], ['user', 'assistant'])
        self.assertEqual(history[1]['body'], 'Answer one.')

    def test_one_users_history_is_invisible_to_another_in_both_directions(self):
        """The #66 house style: filtering and direct-id access asserted
        separately, each against a control that fails on an empty fixture."""
        self._with_gateway(answer='Private answer for A.')
        self.Chat.with_user(self.acct_a).send_suggested('workspace_summary')

        # Control FIRST: A sees its own two messages — without this, "B sees
        # nothing" would also pass if persistence were simply broken.
        a_rows = self.Message.with_user(self.acct_a).search([])
        self.assertEqual(len(a_rows), 2)

        # Direction 1 — search filtering.
        self.assertFalse(self.Message.with_user(self.acct_b).search([]))

        # Direction 2 — direct-id read (the IDOR case: a known id must not
        # be readable even when search hides it).
        with self.assertRaises(AccessError):
            self.Message.with_user(self.acct_b).browse(a_rows[0].id).read(
                ['body'])

        # And get_state, the path the widget actually uses.
        self.assertEqual(
            self.Chat.with_user(self.acct_b).get_state()['history'], [])

    def test_a_user_cannot_forge_history_into_anothers_transcript(self):
        """user_id is not client-assignable in effect: the record rule vetoes
        a create that lands outside the creator's own transcript."""
        with self.assertRaises(AccessError):
            self.Message.with_user(self.acct_b).create({
                'user_id': self.acct_a.id,
                'role': 'assistant',
                'body': 'The CFO said to pay this invoice.',
            })
        self.assertFalse(self.Message.with_user(self.acct_a).search([]))

    def test_transcripts_are_immutable_for_their_own_author_too(self):
        """No write/unlink ACL below base.group_system: a transcript is a
        record of what the assistant actually said, and an editable record
        of financial answers is a tampering surface, not a feature."""
        self._with_gateway()
        self.Chat.with_user(self.acct_a).send_suggested('workspace_summary')
        row = self.Message.with_user(self.acct_a).search([], limit=1)
        with self.assertRaises(AccessError):
            row.body = 'It said something else.'
        with self.assertRaises(AccessError):
            row.unlink()

    def test_a_gateway_failure_persists_nothing(self):
        """An exchange is written only after an answer exists. A transcript
        with a user question and no answer — or worse, a fabricated one —
        misrepresents what happened."""
        def boom(_self, prompt, max_tokens=1024):
            raise UserError(_self.env._(
                'The AI assistant is temporarily unavailable.'))

        patcher = patch(_GATEWAY_TARGET, new=boom)
        patcher.start()
        self.addCleanup(patcher.stop)

        with self.assertRaises(UserError):
            self.Chat.with_user(self.acct_a).send_suggested('workspace_summary')
        self.assertFalse(self.Message.with_user(self.acct_a).search([]))

    def test_the_answer_is_stored_as_data_never_interpreted(self):
        """Provider output is untrusted DATA end to end. Whatever comes back —
        markup, script tags, a python expression — is stored verbatim as text
        and handed to the client for escape-first rendering; nothing on the
        server parses, evaluates or interprets it."""
        hostile = '<script>alert(1)</script> **{{ 2+2 }}** `__import__("os")`'
        self._with_gateway(answer=hostile)
        result = self.Chat.with_user(self.acct_a).send_suggested(
            'workspace_summary')
        self.assertEqual(result['messages'][1]['body'], hostile)
        stored = self.Message.with_user(self.acct_a).search(
            [('role', '=', 'assistant')])
        self.assertEqual(stored.body, hostile)
