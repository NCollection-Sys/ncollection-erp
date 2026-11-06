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

    def test_get_state_can_skip_the_history_read(self):
        """The navigation-driven chip refresh asks for suggestions only —
        include_history=False must not silently keep reading the transcript."""
        self._with_gateway()
        as_a = self.Chat.with_user(self.acct_a)
        as_a.send_suggested('workspace_summary')
        light = as_a.get_state(include_history=False)
        self.assertTrue(light['suggestions'])
        self.assertEqual(light['history'], [])
        # And the full call still returns it — skipping is opt-in.
        self.assertEqual(len(as_a.get_state()['history']), 2)

    def test_a_non_string_or_empty_question_is_refused_not_a_traceback(self):
        """Raw-RPC junk (a list, an int, whitespace) must produce a refusal,
        not a TypeError 500 — and never an empty prompt to the provider."""
        self.env['ir.config_parameter'].sudo().set_param(
            'ncollection_ai.enable_free_text_questions', 'True')
        self.addCleanup(
            self.env['ir.config_parameter'].sudo().set_param,
            'ncollection_ai.enable_free_text_questions', 'False')
        captured = self._with_gateway()
        for junk in (['a', 'list'], 42, '', '   ', None, {'q': 'hi'}):
            with self.assertRaises(UserError, msg=repr(junk)):
                self.Chat.with_user(self.acct_a).send_typed(junk)
        self.assertEqual(captured['calls'], 0)
        self.assertFalse(self.Message.with_user(self.acct_a).search([]))

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
        # Shape, not just existence: get_state reads ['label'] and
        # send_suggested reads ['question'] unguarded, so an entry missing
        # either would crash EVERY widget mount (the reviewer's phrasing:
        # this file is designed to be edited, so the malformed-entry case is
        # the expected change path, not a hypothetical).
        for key, entry in catalog['prompts'].items():
            for field in ('label', 'question'):
                self.assertTrue(
                    isinstance(entry.get(field), str) and entry[field].strip(),
                    'prompt %r must carry a non-empty %r' % (key, field))

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


@tagged('post_install', '-at_install')
class TestChatIntegration(TransactionCase):
    """Proofs added when P5-T06 was ported onto the release line (Option A,
    Arabic, the shipped asset sources). They exercise what changed around the
    widget, not the widget's own logic (TestChatService owns that)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Chat = cls.env['ncollection.ai.chat']
        cls.base_user = cls.env.ref('base.group_user')

    def _read(self, relative):
        import os
        module_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(module_dir, relative), encoding='utf-8') as handle:
            return handle.read()

    # ---------------------------------------------------------- Option A role
    def test_a_tenant_owner_without_system_rights_can_use_the_assistant(self):
        """Option A removes base.group_system / erp_manager from the tenant
        Owner. The assistant must not have depended on them: the Owner reaches
        the gate through its CEO implication, and the history rule binds the
        Owner exactly like everyone else."""
        owner_group = self.env.ref('ncollection_core.group_role_owner')
        owner = self.env['res.users'].create({
            'name': 'chat_owner', 'login': 'chat_owner',
            'group_ids': [(6, 0, [self.base_user.id, owner_group.id])],
        })
        self.assertFalse(owner.has_group('base.group_system'),
                         'fixture invalid: Owner must not hold system rights')
        state = self.Chat.with_user(owner).get_state()
        self.assertTrue(state['enabled'])
        self.assertTrue(state['suggestions'])

    def test_a_plain_internal_user_stays_disabled(self):
        user = self.env['res.users'].create({
            'name': 'chat_plain', 'login': 'chat_plain',
            'group_ids': [(6, 0, [self.base_user.id])],
        })
        self.assertEqual(self.Chat.with_user(user).get_state(),
                         {'enabled': False})

    # ------------------------------------------------------------- languages
    def test_every_chip_label_is_translatable(self):
        """The JSON registry cannot be exported for translation, so each label
        is repeated as a gettext literal in chat.py. A key present in the file
        but missing there would silently stay English for Arabic users."""
        from ..models.chat import _prompt_catalog
        catalog = _prompt_catalog()
        labels = self.Chat._chip_labels()
        self.assertEqual(sorted(labels), sorted(catalog['prompts']),
                         'chat.py _chip_labels() and suggested_prompts.json '
                         'must list exactly the same prompt keys')
        for key, entry in catalog['prompts'].items():
            self.assertEqual(labels[key], entry['label'],
                             'English label of %r drifted between the two '
                             'places' % key)

    def test_every_chip_label_has_an_arabic_translation_on_file(self):
        """Static proof, independent of which languages the test database has
        loaded: each chip label must have a non-empty msgstr in ar.po."""
        import re
        po = self._read('i18n/ar.po')
        for label in self.Chat._chip_labels().values():
            escaped = label.replace('\\', '\\\\').replace('"', '\\"')
            pattern = r'msgid "%s"\nmsgstr "[^"\n]+"' % re.escape(escaped)
            self.assertRegex(po, pattern, 'no Arabic for chip %r' % label)

    def test_chips_are_served_in_the_viewers_language(self):
        """End to end: load Arabic, switch a user to it, and the chips the
        server returns are Arabic while the registry question stays English."""
        self.env['res.lang']._activate_lang('ar_001')
        self.env['ir.module.module'].search(
            [('name', '=', 'ncollection_ai')])._update_translations(['ar_001'])
        accountant = self.env.ref('ncollection_core.group_role_accountant')
        user = self.env['res.users'].create({
            'name': 'chat_ar', 'login': 'chat_ar', 'lang': 'ar_001',
            'group_ids': [(6, 0, [self.base_user.id, accountant.id])],
        })
        # The web client sends the user's language in the RPC context; with_user
        # alone does not carry it, so the test does what call_kw does.
        chat = self.Chat.with_user(user).with_context(lang=user.lang)
        chips = chat.get_state()['suggestions']
        self.assertTrue(chips)
        english =self.Chat.with_context(lang='en_US')._chip_labels()
        for chip in chips:
            self.assertNotEqual(chip['label'], english[chip['key']],
                                'chip %r stayed English' % chip['key'])
        from ..models.chat import _prompt_catalog
        self.assertEqual(
            _prompt_catalog()['prompts']['workspace_summary']['question'],
            'Summarise the financial position of this workspace.')

    # ------------------------------------------------- data and asset hygiene
    def test_registry_text_cannot_carry_markup(self):
        """The registry is hand-edited. Labels and questions are rendered and
        persisted as text; keep them free of markup characters so a careless
        edit can never become an injection vector."""
        from ..models.chat import _prompt_catalog
        for key, entry in _prompt_catalog()['prompts'].items():
            for field in ('label', 'question'):
                self.assertFalse(
                    set(entry[field]) & set('<>&'),
                    '%s.%s contains a markup character' % (key, field))

    def test_the_shipped_sources_have_one_raw_output_and_no_html_sinks(self):
        """XSS guard on the actual files. Provider output is untrusted: the
        only raw-HTML output allowed is the escape-first renderBody() call;
        any other t-out/t-raw, innerHTML, insertAdjacentHTML, outerHTML or
        document.write added later fails this test and needs a review."""
        import re
        template = self._read('static/src/chat/chat_widget.xml')
        script = self._read('static/src/chat/chat_widget.js')
        # Comments may NAME the sinks (the header says there is no innerHTML);
        # only executable code counts.
        script = re.sub(r'/\*.*?\*/', '', script, flags=re.S)
        script = re.sub(r'(?m)^\s*//.*$', '', script)
        self.assertEqual(template.count('t-out='), 1)
        self.assertIn('t-out="renderBody(msg)"', template)
        self.assertNotIn('t-raw', template)
        for sink in ('innerHTML', 'outerHTML', 'insertAdjacentHTML',
                     'document.write', 'eval(', 'new Function'):
            self.assertNotIn(sink, script, 'HTML/code sink %r added' % sink)
        # markup() is the one deliberate trust boundary, and it is fed only
        # by the escape-first renderer.
        self.assertEqual(script.count('markup('), 1)

    def test_visible_template_text_is_translatable_not_a_js_expression(self):
        """Odoo extracts static template text/attributes for translation but
        not JS expressions inside t-att-*: a literal in one would stay English
        in Arabic."""
        import re
        template = self._read('static/src/chat/chat_widget.xml')
        pattern = r't-att-(?:title|placeholder|aria-label)="([^"]*)"'
        for expression in re.findall(pattern, template):
            self.assertNotRegex(expression, r"['\"]",
                                'literal text in a t-att expression: %s'
                                % expression)

    # ------------------------------------------------------------ performance
    def test_the_navigation_refresh_is_cheap(self):
        """Route changes call get_state(include_history=False): the whole call
        stays a handful of queries regardless of how long the history is, and
        the full read stays bounded."""
        accountant = self.env.ref('ncollection_core.group_role_accountant')
        user = self.env['res.users'].create({
            'name': 'chat_perf', 'login': 'chat_perf',
            'group_ids': [(6, 0, [self.base_user.id, accountant.id])],
        })
        self.env['ncollection.ai.chat.message'].with_user(user).create(
            [{'role': 'user', 'body': 'q%d' % i} for i in range(60)])
        chat = self.Chat.with_user(user)
        chat.get_state(include_history=False)  # warm the caches
        before = self.cr.sql_log_count
        chat.get_state(include_history=False)
        self.assertLessEqual(self.cr.sql_log_count - before, 4)
        before = self.cr.sql_log_count
        state = chat.get_state()
        self.assertLessEqual(self.cr.sql_log_count - before, 6)
        self.assertEqual(len(state['history']), 40, 'history must stay bounded')
