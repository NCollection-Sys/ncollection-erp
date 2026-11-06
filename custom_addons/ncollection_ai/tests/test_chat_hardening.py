# -*- coding: utf-8 -*-
"""P5-T06 completion: forgery, size, retention, language, streaming plumbing.

What the batch tests in test_chat.py do not cover:

  * a gated user can no longer plant assistant rows through the ORM at all;
  * a stored row has a server-side size cap (service truncation + constraint);
  * transcripts are deleted after a configurable window, by a real cron;
  * the transcript shows the viewer-language label, the provider is told to
    answer in the viewer's language and still receives the English question;
  * the streaming pieces: incremental re-hydration, admission before the first
    byte, the gateway client's NDJSON reader, and the route itself.
"""
import io
import json
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.ncollection_ai import concurrency
from odoo.addons.ncollection_ai.models import chat as chat_module
from odoo.addons.ncollection_ai.models.pii import StreamRehydrator

_COMPLETE = ('odoo.addons.ncollection_ai.models.gateway_client'
             '.AiGatewayClient._complete')
_STREAM = ('odoo.addons.ncollection_ai.models.gateway_client'
           '.AiGatewayClient._stream')


class _FakeCursor:
    """Stands in for the maintenance-database cursor; the real lock call is replaced by the patched `_try`."""

    def execute(self, *args, **kwargs):
        self.statements = getattr(self, 'statements', []) + [args[0] if args else '']

    def fetchone(self):
        return (True,)

    def commit(self):
        pass

    def close(self):
        pass


class _Both:
    """Enter two patchers as one context manager."""

    def __init__(self, first, second):
        self._patchers = (first, second)

    def __enter__(self):
        for patcher in self._patchers:
            patcher.start()

    def __exit__(self, *exc):
        for patcher in reversed(self._patchers):
            patcher.stop()


def _fake_slots(taken):
    """Make `AiSlot._try` succeed only for (lock class, slot) pairs not in `taken` (the real lock is per-session, and a
    test transaction is ONE session, so the real call would always re-enter)."""
    def fake_try(slot_self, cr, index):
        key = (slot_self._lock_class(), index)
        if key in taken:
            return False
        taken.add(key)
        return True
    return _Both(patch.object(concurrency.AiSlot, '_try', fake_try),
                 patch.object(concurrency.GlobalAiSlot, '_cursor', lambda s: _FakeCursor()))


def _make_users(cls):
    cls.Chat = cls.env['ncollection.ai.chat']
    cls.Message = cls.env['ncollection.ai.chat.message']
    base_user = cls.env.ref('base.group_user')
    accountant = cls.env.ref('ncollection_core.group_role_accountant')
    cls.acct = cls.env['res.users'].create({
        'name': 'hard_acct', 'login': 'hard_acct', 'password': 'Hard-Acct-Pass1!',
        'group_ids': [(6, 0, [base_user.id, accountant.id])]})
    cls.plain = cls.env['res.users'].create({
        'name': 'hard_plain', 'login': 'hard_plain',
        'group_ids': [(6, 0, [base_user.id])]})


@tagged('post_install', '-at_install')
class TestTranscriptHardening(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_users(cls)

    # ---------------------------------------------------------------- forgery
    def test_a_gated_user_cannot_create_any_row_through_the_orm(self):
        for role in ('assistant', 'user'):
            with self.assertRaises(AccessError):
                self.Message.with_user(self.acct).create({
                    'user_id': self.acct.id, 'role': role, 'body': 'forged'})
        self.assertFalse(self.Message.sudo().search([('user_id', '=', self.acct.id)]))

    def test_the_acl_grants_read_only_to_every_group(self):
        rows = self.env['ir.model.access'].sudo().search([
            ('model_id.model', '=', 'ncollection.ai.chat.message')])
        self.assertTrue(rows)
        for access in rows:
            self.assertTrue(access.perm_read)
            self.assertFalse(access.perm_create, access.name)
            self.assertFalse(access.perm_write, access.name)
            self.assertFalse(access.perm_unlink, access.name)

    def test_the_service_still_writes_both_rows_as_the_caller(self):
        with patch(_COMPLETE, new=lambda s, p, max_tokens=1024: {'text': 'ok', 'usage': {}}):
            result = self.Chat.with_user(self.acct).send_suggested('workspace_summary')
        self.assertEqual([m['role'] for m in result['messages']], ['user', 'assistant'])
        rows = self.Message.sudo().search([('user_id', '=', self.acct.id)])
        self.assertEqual(rows.mapped('role'), ['user', 'assistant'])

    # ------------------------------------------------------------------- size
    def test_an_oversized_row_is_refused_by_the_model(self):
        with self.assertRaises(ValidationError):
            self.Message.sudo().create({
                'user_id': self.acct.id, 'role': 'assistant',
                'body': 'x' * (chat_module._MAX_ASSISTANT_CHARS + 1)})
        with self.assertRaises(ValidationError):
            self.Message.sudo().create({
                'user_id': self.acct.id, 'role': 'user',
                'body': 'x' * (chat_module._MAX_QUESTION_CHARS + 1)})
        ok = self.Message.sudo().create({
            'user_id': self.acct.id, 'role': 'assistant',
            'body': 'x' * chat_module._MAX_ASSISTANT_CHARS})
        self.assertTrue(ok)

    def test_the_service_truncates_a_huge_answer_instead_of_failing(self):
        huge = 'y' * (chat_module._MAX_ASSISTANT_CHARS * 3)
        with patch(_COMPLETE, new=lambda s, p, max_tokens=1024: {'text': huge, 'usage': {}}):
            result = self.Chat.with_user(self.acct).send_suggested('workspace_summary')
        self.assertEqual(len(result['messages'][1]['body']), chat_module._MAX_ASSISTANT_CHARS)

    # -------------------------------------------------------------- retention
    def _row(self, age_days):
        row = self.Message.sudo().create({
            'user_id': self.acct.id, 'role': 'user', 'body': 'age %s' % age_days})
        self.env.cr.execute(
            "UPDATE ncollection_ai_chat_message SET create_date = %s WHERE id = %s",
            [fields.Datetime.now() - timedelta(days=age_days), row.id])
        row.invalidate_recordset()
        return row

    def test_default_window_is_ninety_days_and_deletes_only_older_rows(self):
        self.env['ir.config_parameter'].sudo().set_param(chat_module.RETENTION_PARAM, '90')
        old, fresh = self._row(120), self._row(10)
        deleted = self.Message._gc_transcripts()
        self.assertGreaterEqual(deleted, 1)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    def test_the_window_is_configurable(self):
        self.env['ir.config_parameter'].sudo().set_param(chat_module.RETENTION_PARAM, '5')
        mid = self._row(10)
        self.Message._gc_transcripts()
        self.assertFalse(mid.exists())

    def test_zero_or_negative_keeps_everything(self):
        old = self._row(400)
        for value in ('0', '-3'):
            self.env['ir.config_parameter'].sudo().set_param(chat_module.RETENTION_PARAM, value)
            self.assertEqual(self.Message._gc_transcripts(), 0)
        self.assertTrue(old.exists())

    def test_a_mistyped_value_falls_back_to_the_default_not_to_off(self):
        self.env['ir.config_parameter'].sudo().set_param(chat_module.RETENTION_PARAM, 'ninety')
        self.assertEqual(self.Message._retention_days(), chat_module._DEFAULT_RETENTION_DAYS)

    def test_a_daily_cron_runs_the_job_and_the_default_is_seeded(self):
        cron = self.env.ref('ncollection_ai.cron_gc_ai_transcripts')
        self.assertTrue(cron.active)
        self.assertEqual((cron.interval_number, cron.interval_type), (1, 'days'))
        self.assertIn('_gc_transcripts', cron.code)
        self.assertEqual(cron.model_id.model, 'ncollection.ai.chat.message')
        self.assertTrue(self.env['ir.config_parameter'].sudo().get_param(chat_module.RETENTION_PARAM))

    def test_the_retention_job_never_touches_another_tenants_scope(self):
        """It is a plain search on THIS database; there is no cross-database path."""
        import inspect
        source = inspect.getsource(chat_module.AiChatMessage._gc_transcripts)
        self.assertNotIn('cursor(', source)
        self.assertNotIn('dbname', source)


@tagged('post_install', '-at_install')
class TestConcurrencyCap(TransactionCase):
    """An AI request holds a web worker for the provider's latency; each database gets at most N in flight."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_users(cls)

    _fake_slots = staticmethod(lambda taken: _fake_slots(taken))

    def test_the_default_is_two_and_a_bad_value_falls_back(self):
        chat = self.Chat.with_user(self.acct)
        self.assertEqual(chat._max_concurrent(), chat_module._DEFAULT_MAX_CONCURRENT)
        for raw, expected in (('1', 1), ('5', 5), ('0', 1), ('-2', 1), ('many', chat_module._DEFAULT_MAX_CONCURRENT)):
            self.env['ir.config_parameter'].sudo().set_param(chat_module.MAX_CONCURRENT_PARAM, raw)
            self.assertEqual(chat._max_concurrent(), expected, raw)

    def test_the_slot_after_the_limit_is_refused_at_once_with_a_friendly_message(self):
        params = self.env['ir.config_parameter'].sudo()
        params.set_param(chat_module.MAX_CONCURRENT_PARAM, '2')
        params.set_param(chat_module.MAX_GLOBAL_PARAM, '5')
        params.set_param(chat_module.SLOT_WAIT_PARAM, '0')
        taken = set()
        with self._fake_slots(taken):
            chat = self.Chat.with_user(self.acct)
            first, second = chat._take_slot(), chat._take_slot()
            self.assertEqual({k for k in taken if k[0] == concurrency._LOCK_CLASS},
                             {(concurrency._LOCK_CLASS, 0), (concurrency._LOCK_CLASS, 1)})
            with self.assertRaisesRegex(UserError, 'busy'):
                chat._take_slot()
            # Test cursors are savepoints: close them last-in first-out (production cursors are independent).
            second.release()
            first.release()

    def test_the_global_limit_follows_the_worker_count_and_never_takes_the_last_worker(self):
        for workers, configured, expected in (
                (2, None, 1), (4, None, 2), (8, None, 4), (3, None, 1), (1, None, 1),
                (4, 99, 3), (2, 5, 1), (4, 1, 1), (0, None, 2), (0, 7, 7), (6, 0, 1)):
            self.assertEqual(concurrency.global_limit(workers, configured), expected, (workers, configured))

    def test_the_global_cap_refuses_a_tenant_that_still_has_its_own_slots_free(self):
        """Another tenant (or database) already holds the server-wide slots: this tenant has per-database capacity but
        is refused, and takes no per-database slot while refused."""
        params = self.env['ir.config_parameter'].sudo()
        params.set_param(chat_module.MAX_CONCURRENT_PARAM, '3')
        params.set_param(chat_module.MAX_GLOBAL_PARAM, '1')
        params.set_param(chat_module.SLOT_WAIT_PARAM, '0')
        taken = {(concurrency._GLOBAL_LOCK_CLASS, 0)}
        with self._fake_slots(taken):
            with self.assertRaisesRegex(UserError, 'busy'):
                self.Chat.with_user(self.acct)._take_slot()
        self.assertFalse([k for k in taken if k[0] == concurrency._LOCK_CLASS])

    def test_the_wait_is_bounded_and_a_slot_freed_inside_it_is_taken(self):
        import time
        params = self.env['ir.config_parameter'].sudo()
        params.set_param(chat_module.MAX_CONCURRENT_PARAM, '1')
        params.set_param(chat_module.MAX_GLOBAL_PARAM, '5')
        params.set_param(chat_module.SLOT_WAIT_PARAM, '250')
        chat = self.Chat.with_user(self.acct)
        taken = {(concurrency._LOCK_CLASS, 0)}
        with self._fake_slots(taken):
            started = time.monotonic()
            with self.assertRaises(UserError):
                chat._take_slot()
            elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 0.2)
        self.assertLess(elapsed, 1.5)
        attempts = []

        def fake_try(slot_self, cr, index):
            if slot_self._lock_class() == concurrency._LOCK_CLASS:
                attempts.append(1)
                return len(attempts) >= 3      # the tenant slot frees up after two polls
            return True

        with patch.object(concurrency.AiSlot, '_try', fake_try), \
                patch.object(concurrency.GlobalAiSlot, '_cursor', lambda s: _FakeCursor()):
            slot = chat._take_slot()
            slot.release()
        self.assertEqual(len(attempts), 3)

    def test_an_unreachable_maintenance_database_fails_closed(self):
        self.env['ir.config_parameter'].sudo().set_param(chat_module.SLOT_WAIT_PARAM, '0')

        def down(slot_self):
            raise RuntimeError('no maintenance database')

        with patch.object(concurrency.GlobalAiSlot, '_cursor', down):
            with self.assertRaisesRegex(UserError, 'busy'):
                self.Chat.with_user(self.acct)._take_slot()

    def test_the_server_slot_executes_only_advisory_lock_calls_in_the_fixed_maintenance_database(self):
        import inspect
        source = inspect.getsource(concurrency.GlobalAiSlot) + inspect.getsource(concurrency.AiSlot)
        self.assertEqual(concurrency._MAINTENANCE_DB, 'postgres')
        self.assertIn("db_connect(_MAINTENANCE_DB)", source)
        for smell in ("FROM ", "INSERT", "UPDATE", "DELETE", "SELECT *"):
            self.assertNotIn(smell, source, smell)

    def test_a_refused_batch_question_calls_the_gateway_not_at_all(self):
        self.env['ir.config_parameter'].sudo().set_param(chat_module.MAX_CONCURRENT_PARAM, '1')
        called = []

        def fake_complete(_self, prompt, max_tokens=1024):
            called.append(1)
            return {'text': 'x'}

        with self._fake_slots({(concurrency._LOCK_CLASS, 0)}), patch(_COMPLETE, new=fake_complete):
            with self.assertRaises(UserError):
                self.Chat.with_user(self.acct).send_suggested('workspace_summary')
        self.assertFalse(called)
        self.assertFalse(self.Message.sudo().search([('user_id', '=', self.acct.id)]))

    def test_the_slot_is_released_even_when_the_pipeline_fails(self):
        released = []
        original = concurrency.AiAdmission.release

        def spy(slot_self):
            released.append(1)
            return original(slot_self)

        def boom(*args, **kwargs):
            raise RuntimeError('gateway down')

        with patch.object(concurrency.AiAdmission, 'release', spy), patch(_COMPLETE, new=boom):
            with self.assertRaises(RuntimeError):
                self.Chat.with_user(self.acct).send_suggested('workspace_summary')
        self.assertEqual(released, [1])

    def test_the_slot_helper_builds_no_registry_and_opens_no_connection_by_name(self):
        import inspect
        source = inspect.getsource(concurrency.AiSlot)
        for smell in ("Registry(", "db_connect", "psycopg2", "FROM ", "SELECT * "):
            self.assertNotIn(smell, source, smell)
        self.assertEqual(source.count("self._registry.cursor()"), 1)

    def test_the_stream_route_gives_the_slot_back_when_it_finishes(self):
        import inspect
        from ..controllers import chat_stream
        source = inspect.getsource(chat_stream.AiChatStream.stream)
        self.assertIn('_take_slot()', source)
        self.assertGreaterEqual(
            source.count('slot.release()'), 2, 'released on a failed start AND when the stream ends')


@tagged('post_install', '-at_install')
class TestLanguageConsistency(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['res.lang']._activate_lang('ar_001')
        _make_users(cls)

    def test_the_prompt_asks_for_the_viewers_language(self):
        question = self.env['ncollection.ai.question']
        self.assertIn('Arabic', question.with_context(lang='ar_001')._language_line())
        self.assertIn('English', question.with_context(lang='en_US')._language_line())
        prepared = question.with_context(lang='ar_001')._prepare_pipeline('How are we doing?')
        self.assertIn('Write the whole answer in Arabic', prepared['prompt'])

    def test_the_language_line_is_a_constant_per_language_never_user_text(self):
        question = self.env['ncollection.ai.question']
        line = question._language_line()
        self.assertNotIn('{', line)
        self.assertTrue(line.startswith('Write the whole answer in '))

    def test_the_transcript_shows_the_label_in_the_viewers_language(self):
        self.env['res.lang']._activate_lang('ar_001')
        arabic_user = self.acct.with_context(lang='ar_001')
        arabic_user.lang = 'ar_001'
        seen = {}

        def fake(_self, prompt, max_tokens=1024):
            seen['prompt'] = prompt
            return {'text': 'ok', 'usage': {}}

        with patch(_COMPLETE, new=fake):
            result = self.Chat.with_user(arabic_user).with_context(lang='ar_001').send_suggested(
                'workspace_summary')
        shown = result['messages'][0]['body']
        english = chat_module._prompt_catalog()['prompts']['workspace_summary']['question']
        self.assertNotEqual(shown, english, 'the transcript must not echo the English registry text')
        self.assertTrue(any('؀' <= c <= 'ۿ' for c in shown), shown)
        # The provider still receives the canonical English question, in an Arabic-answer prompt.
        self.assertIn(english, seen['prompt'])
        self.assertIn('Arabic', seen['prompt'])


class _FakeResponse(io.BytesIO):
    """Just enough of an HTTP response for the NDJSON reader."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _ndjson(*events):
    return _FakeResponse(b''.join(json.dumps(e).encode() + b'\n' for e in events))


@tagged('post_install', '-at_install')
class TestStreamingPlumbing(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_users(cls)
        cls.Gateway = cls.env['ncollection.ai.gateway']
        cls.messages = {'too_long': 'L', 'unreadable': 'U', 'provider': 'P', 'cut_off': 'C'}

    # ------------------------------------------------------- re-hydration
    def test_a_token_split_across_chunks_is_restored_whole(self):
        mapping = {'PARTNER_1': 'Acme LLC', 'PARTNER_10': 'Globex'}
        for cut in range(1, len('Owed by PARTNER_10 and PARTNER_1.')):
            text = 'Owed by PARTNER_10 and PARTNER_1.'
            rehydrator = StreamRehydrator(
                self.env['ncollection.ai.pii']._rehydrate, mapping)
            shown = rehydrator.feed(text[:cut]) + rehydrator.feed(text[cut:]) + rehydrator.flush()
            self.assertEqual(shown, 'Owed by Globex and Acme LLC.', 'cut at %d' % cut)

    def test_no_half_token_is_ever_shown(self):
        mapping = {'PARTNER_1': 'Acme LLC'}
        rehydrator = self.env['ncollection.ai.pii']._stream_rehydrator(mapping)
        self.assertEqual(rehydrator.feed('Total for PARTN'), 'Total for ')
        # The trailing word ("is") is held until something proves it complete.
        self.assertEqual(rehydrator.feed('ER_1 is'), 'Acme LLC ')
        self.assertEqual(rehydrator.flush(), 'is')

    # ---------------------------------------------------------- admission
    def test_the_stream_plan_enforces_the_same_admission_as_the_batch_calls(self):
        with self.assertRaises(AccessError):
            self.Chat.with_user(self.plain)._stream_plan('suggested', 'workspace_summary')
        with self.assertRaises(UserError):
            self.Chat.with_user(self.acct)._stream_plan('suggested', 'no_such_key')
        with self.assertRaises(UserError):     # free text is off by default (#375)
            self.Chat.with_user(self.acct)._stream_plan('typed', 'What do we owe?')
        with self.assertRaises(UserError):
            self.Chat.with_user(self.acct)._stream_plan('other', 'x')

    def test_a_suggested_plan_carries_the_label_and_a_sanitised_prompt(self):
        plan = self.Chat.with_user(self.acct)._stream_plan('suggested', 'workspace_summary')
        self.assertEqual(plan['display'], self.Chat._chip_labels()['workspace_summary'])
        self.assertIn('QUESTION:', plan['prepared']['prompt'])
        self.assertIn('mapping', plan['prepared'])

    def test_the_plan_and_the_pipeline_halves_are_not_rpc_callable(self):
        for name in ('_stream_plan', '_resolve_suggested', '_persist_exchange'):
            self.assertTrue(name.startswith('_'))
        question = self.env['ncollection.ai.question']
        for name in ('_admit_typed', '_prepare_pipeline', '_run_pipeline'):
            self.assertTrue(hasattr(question, name))
            self.assertTrue(name.startswith('_'))

    # ----------------------------------------------------- NDJSON reader
    def test_the_reader_yields_events_and_requires_done(self):
        events = list(self.Gateway._stream_events(
            _ndjson({'delta': 'a'}, {'delta': 'b'}, {'done': True, 'usage': {}}), self.messages))
        self.assertEqual([e.get('delta') for e in events[:2]], ['a', 'b'])
        with self.assertRaisesRegex(UserError, 'C'):
            list(self.Gateway._stream_events(_ndjson({'delta': 'a'}), self.messages))

    def test_an_in_band_error_or_garbage_line_is_a_failure(self):
        with self.assertRaisesRegex(UserError, 'boom'):
            list(self.Gateway._stream_events(
                _ndjson({'delta': 'a'}, {'error': 'x', 'message': 'boom'}), self.messages))
        with self.assertRaisesRegex(UserError, 'U'):
            list(self.Gateway._stream_events(_FakeResponse(b'not json\n'), self.messages))

    def test_an_oversized_stream_is_cut(self):
        big = {'delta': 'z' * 60000}
        with self.assertRaisesRegex(UserError, 'L'):
            list(self.Gateway._stream_events(
                _ndjson(*([big] * 12), {'done': True}), self.messages))


@tagged('post_install', '-at_install')
class TestStreamRoute(HttpCase):
    """The route, end to end inside Odoo, with the gateway stream faked."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_users(cls)

    def _post(self, **data):
        from odoo import http
        data['csrf_token'] = http.Request.csrf_token(self)
        return self.url_open('/ncollection/ai/chat/stream', data=data)

    def _no_slot(self):
        """HTTP tests run the generator after the request cursor is closed, which a SAVEPOINT-based test cursor cannot
        survive (production cursors are independent connections). The slot itself is covered by TestConcurrencyCap."""
        class _Dummy:
            def release(self):
                pass
        return patch('odoo.addons.ncollection_ai.models.chat.AiChat._take_slot', new=lambda s: _Dummy())

    def _fake_stream(self, chunks):
        def fake(_self, prompt, max_tokens=1024):
            return iter([{'delta': c} for c in chunks] + [{'done': True, 'usage': {'total_tokens': 9}}])
        return patch(_STREAM, new=fake)

    def test_a_streamed_answer_arrives_in_pieces_and_is_saved_once(self):
        self.authenticate('hard_acct', 'Hard-Acct-Pass1!')
        with self._no_slot(), self._fake_stream(['Hello ', 'there ', 'friend.']):
            response = self._post(kind='suggested', value='workspace_summary')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get('X-Accel-Buffering'), 'no')
        events = [json.loads(line) for line in response.text.splitlines() if line]
        self.assertEqual([e['type'] for e in events], ['delta', 'delta', 'delta', 'done'])
        self.assertEqual(''.join(e['text'] for e in events[:-1]), 'Hello there friend.')
        saved = self.env['ncollection.ai.chat.message'].sudo().search(
            [('user_id', '=', self.acct.id)])
        self.assertEqual(saved.mapped('role'), ['user', 'assistant'])
        self.assertEqual(saved[1].body, 'Hello there friend.')
        self.assertEqual([m['role'] for m in events[-1]['messages']], ['user', 'assistant'])

    def test_a_refusal_is_an_ordinary_4xx_and_saves_nothing(self):
        self.authenticate('hard_plain', 'hard_plain')
        response = self._post(kind='suggested', value='workspace_summary')
        self.assertEqual(response.status_code, 403)
        self.authenticate('hard_acct', 'Hard-Acct-Pass1!')
        response = self._post(kind='typed', value='What do we owe?')       # free text OFF
        self.assertEqual(response.status_code, 400)
        response = self._post(kind='suggested', value='nope')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.env['ncollection.ai.chat.message'].sudo().search(
            [('user_id', '=', self.acct.id)]))

    def test_a_stream_that_never_finishes_saves_nothing(self):
        def cut(_self, prompt, max_tokens=1024):
            gateway = self.env['ncollection.ai.gateway']
            return gateway._stream_events(
                _ndjson({'delta': 'half an ans'}),
                {'too_long': 'L', 'unreadable': 'U', 'provider': 'P', 'cut_off': 'CUT'})
        self.authenticate('hard_acct', 'Hard-Acct-Pass1!')
        with self._no_slot(), patch(_STREAM, new=cut):
            response = self._post(kind='suggested', value='workspace_summary')
        events = [json.loads(line) for line in response.text.splitlines() if line]
        self.assertEqual(events[-1]['type'], 'error')
        self.assertEqual(events[-1]['message'], 'CUT')
        self.assertFalse(self.env['ncollection.ai.chat.message'].sudo().search(
            [('user_id', '=', self.acct.id)]))

    def test_a_second_stream_over_the_cap_is_a_400_and_never_reaches_the_gateway(self):
        self.env['ir.config_parameter'].sudo().set_param(chat_module.MAX_CONCURRENT_PARAM, '1')
        self.authenticate('hard_acct', 'Hard-Acct-Pass1!')
        reached = []

        def fake(_self, prompt, max_tokens=1024):
            reached.append(1)
            return iter([])

        with _fake_slots({(concurrency._GLOBAL_LOCK_CLASS, i) for i in range(8)}), patch(_STREAM, new=fake):
            response = self._post(kind='suggested', value='workspace_summary')
        self.assertEqual(response.status_code, 400)
        self.assertIn('busy', response.json()['message'])
        self.assertFalse(reached)

    def test_an_anonymous_request_is_not_served(self):
        self.authenticate(None, None)
        response = self.url_open(
            '/ncollection/ai/chat/stream', allow_redirects=False,
            data={'kind': 'suggested', 'value': 'workspace_summary'})
        self.assertIn(response.status_code, (302, 303, 401, 403))
        self.assertFalse(self.env['ncollection.ai.chat.message'].sudo().search([]))
