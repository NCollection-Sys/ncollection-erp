# -*- coding: utf-8 -*-
"""The release compatibility gate (release_gate.py): which databases may receive traffic from the running code.

The decision is pinned here against the very state that produced the recurring HTTP 500 (a database recorded at an
older module version than the code, R-037), fabricated INSIDE the test transaction. The gate's HTTP behaviour (a 503
maintenance page instead of the 500, the platform's status call, holds, Company Entry into a stale tenant) is proven
against a real server in scripts/upgrade/verify_upgrade.sh arm 8, because Odoo's test server runs with the gate off
(``test_enable``): its at-install HTTP tests run while other modules are still being installed.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
from types import SimpleNamespace
from unittest.mock import patch

import psycopg2.errors

from odoo import http
from odoo.addons.base.models.ir_qweb import QWebError, QWebErrorInfo
from odoo.tests import TransactionCase, tagged

from odoo.addons.ncollection_core import release_gate as gate
from odoo.addons.ncollection_core.hooks import _hold_for_provisioning

RELEASE = {'modules': {'ncollection_core': '19.0.1.23.0', 'ncollection_branding': '19.0.1.12.0',
                       'ncollection_auth': '19.0.1.3.0', 'queue_job': '19.0.2.0.1'},
           'fingerprint': 'f' * 64}
CURRENT = [('base', 'installed', '19.0.1.3'), ('ncollection_core', 'installed', '19.0.1.23.0'),
           ('ncollection_branding', 'installed', '19.0.1.12.0'), ('ncollection_auth', 'installed', '19.0.1.3.0')]


def _with(rows, **changes):
    return [(name, changes.get(name, (state, version))[0], changes.get(name, (state, version))[1])
            for name, state, version in rows]


@tagged('post_install', '-at_install')
class TestReleaseGateDecision(TransactionCase):
    """``evaluate``: pure, so every case is exact."""

    def test_a_database_that_matches_the_code_is_ready(self):
        result = gate.evaluate(CURRENT, None, False, RELEASE)
        self.assertEqual(result['state'], gate.READY)
        self.assertTrue(result['compatible'])
        self.assertEqual(result['behind'], [])

    def test_case_a_one_module_behind_is_upgrade_required_and_named(self):
        rows = _with(CURRENT, ncollection_branding=('installed', '19.0.1.10.0'))
        result = gate.evaluate(rows, None, False, RELEASE)
        self.assertEqual(result['state'], gate.UPGRADE_REQUIRED)
        self.assertFalse(result['compatible'])
        self.assertEqual(result['behind'], [('ncollection_branding', '19.0.1.10.0', '19.0.1.12.0')])

    def test_case_b_several_modules_behind_are_all_named_together(self):
        """The Atlas-eg shape: core, branding and auth behind. All of them, in one list, never core alone."""
        rows = _with(CURRENT, ncollection_core=('installed', '19.0.1.18.0'),
                     ncollection_branding=('installed', '19.0.1.8.0'), ncollection_auth=('installed', '19.0.1.2.0'))
        result = gate.evaluate(rows, None, False, RELEASE)
        self.assertEqual(result['state'], gate.UPGRADE_REQUIRED)
        self.assertEqual([row[0] for row in result['behind']],
                         ['ncollection_auth', 'ncollection_branding', 'ncollection_core'])

    def test_versions_compare_as_versions_not_text(self):
        rows = _with(CURRENT, ncollection_branding=('installed', '19.0.1.9.0'))
        self.assertEqual(gate.evaluate(rows, None, False, RELEASE)['state'], gate.UPGRADE_REQUIRED)

    def test_a_database_ahead_of_the_code_is_not_reported_behind(self):
        rows = _with(CURRENT, ncollection_core=('installed', '19.0.1.99.0'))
        self.assertEqual(gate.evaluate(rows, None, False, RELEASE)['state'], gate.READY)

    def test_an_odoo_core_module_is_outside_the_release_universe(self):
        rows = CURRENT + [('web', 'installed', '19.0.0.1')]
        self.assertEqual(gate.evaluate(rows, None, False, RELEASE)['state'], gate.READY)

    def test_a_module_being_installed_or_upgraded_is_upgrading(self):
        rows = _with(CURRENT, ncollection_auth=('to upgrade', '19.0.1.3.0'))
        result = gate.evaluate(rows, None, False, RELEASE)
        self.assertEqual(result['state'], gate.UPGRADING)
        self.assertFalse(result['compatible'])

    def test_a_database_being_created_is_provisioning(self):
        self.assertEqual(gate.evaluate(None, None, False, RELEASE)['state'], gate.PROVISIONING)

    def test_an_installed_module_that_is_not_deployed_blocks(self):
        rows = CURRENT + [('ncollection_uae_einvoicing', 'installed', '19.0.1.0.0')]
        result = gate.evaluate(rows, None, False, RELEASE)
        self.assertEqual(result['state'], gate.BLOCKED)
        self.assertEqual(result['missing'], ['ncollection_uae_einvoicing'])

    def test_a_module_the_code_auto_installs_and_the_database_lacks_requires_an_upgrade(self):
        """Parity: an OLD tenant (sale installed before the Sales documents module existed) is not READY. Odoo
        auto-installs only while a trigger is itself being installed, so nothing else would ever give it the module
        a tenant created today has. The fixed point follows a chain (one auto-install enabling the next)."""
        release = dict(RELEASE, modules=dict(RELEASE['modules'], ncollection_sale_documents='19.0.1.0.0',
                                             ncollection_sale_extra='19.0.1.0.0'),
                       auto_install={'ncollection_sale_documents': ('sale', 'ncollection_core'),
                                     'ncollection_sale_extra': ('ncollection_sale_documents',),
                                     'ncollection_stock_barcode': ('stock',)})
        rows = CURRENT + [('sale', 'installed', '19.0.1.2')]
        result = gate.evaluate(rows, None, False, release)
        self.assertEqual(result['state'], gate.UPGRADE_REQUIRED)
        self.assertFalse(result['compatible'])
        self.assertEqual(result['absent'], ['ncollection_sale_documents', 'ncollection_sale_extra'],
                         'stock is not installed here: the barcode module is not owed')
        self.assertEqual(result['required'], [], 'nothing is behind: the -u list stays exact')
        rows += [('ncollection_sale_documents', 'installed', '19.0.1.0.0'),
                 ('ncollection_sale_extra', 'installed', '19.0.1.0.0')]
        self.assertEqual(gate.evaluate(rows, None, False, release)['state'], gate.READY)

    def test_an_auto_install_left_mid_install_is_transitional_not_absent(self):
        release = dict(RELEASE, auto_install={'ncollection_sale_documents': ('sale',)})
        rows = CURRENT + [('sale', 'installed', '19.0.1.2'), ('ncollection_sale_documents', 'to install', '')]
        result = gate.evaluate(rows, None, False, release)
        self.assertEqual(result['state'], gate.UPGRADING)
        self.assertEqual(result['absent'], [])

    def test_the_release_reads_auto_install_triggers_of_our_modules_only(self):
        auto = gate.release_manifest()['auto_install']
        self.assertIn('ncollection_sale_documents', auto)
        self.assertIn('sale', auto['ncollection_sale_documents'])
        self.assertFalse([name for name in auto if not name.startswith('ncollection_')])

    def test_a_schema_gap_found_at_registry_load_blocks(self):
        self.assertEqual(gate.evaluate(CURRENT, None, True, RELEASE)['state'], gate.BLOCKED)

    def test_holds_are_explicit_states_and_only_validation_holds_are_passable(self):
        for hold, state, passable in (('provisioning', gate.PROVISIONING, True), ('upgrading', gate.UPGRADING, True),
                                      ('upgrade_failed:boom', gate.UPGRADE_FAILED, False),
                                      ('blocked:by ops', gate.BLOCKED, False)):
            with self.subTest(hold=hold):
                result = gate.evaluate(CURRENT, hold, False, RELEASE)
                self.assertEqual(result['state'], state)
                self.assertEqual(result['passable'], passable)
        self.assertEqual(gate.evaluate(CURRENT, 'blocked:by ops', False, RELEASE)['hold_reason'], 'by ops')

    def test_a_hold_never_makes_an_incompatible_database_passable(self):
        rows = _with(CURRENT, ncollection_core=('installed', '19.0.1.1.0'))
        result = gate.evaluate(rows, 'upgrading', False, RELEASE)
        self.assertFalse(result['passable'], 'the platform bearer passes a hold, never code that is ahead')

    def test_an_unknown_hold_value_is_ignored(self):
        self.assertEqual(gate.evaluate(CURRENT, 'whatever', False, RELEASE)['state'], gate.READY)

    def test_the_fingerprints_are_deterministic(self):
        one = gate.evaluate(CURRENT, None, False, RELEASE)['db_fingerprint']
        self.assertEqual(one, gate.evaluate(list(reversed(CURRENT)), None, False, RELEASE)['db_fingerprint'])
        other = gate.evaluate(_with(CURRENT, ncollection_auth=('installed', '19.0.1.2.0')), None, False, RELEASE)
        self.assertNotEqual(one, other['db_fingerprint'])

    def test_the_role_comes_from_the_database_s_own_modules_never_its_name(self):
        self.assertEqual(gate.evaluate(CURRENT, None, False, RELEASE)['role'], gate.TENANT)
        platform = CURRENT + [(gate.PLATFORM_MODULE, 'installed', '19.0.6.19.0')]
        self.assertEqual(gate.evaluate(platform, None, False, RELEASE)['role'], gate.PLATFORM)
        self.assertEqual(gate.evaluate(CURRENT + [(gate.PLATFORM_MODULE, 'to remove', '1')], None, False,
                                       RELEASE)['role'], gate.TENANT)

    def test_a_stale_platform_is_blocked_exactly_like_a_stale_tenant(self):
        """No bypass for the control plane: the role changes who may ask, never whether stale code may serve it."""
        stale = _with(CURRENT, ncollection_core=('installed', '19.0.1.22.0'))
        for extra in ([], [(gate.PLATFORM_MODULE, 'installed', '19.0.6.19.0')]):
            with self.subTest(role='platform' if extra else 'tenant'):
                result = gate.evaluate(stale + extra, None, False, RELEASE)
                self.assertEqual(result['state'], gate.UPGRADE_REQUIRED)
                self.assertFalse(result['compatible'])
                self.assertFalse(result['passable'])

    def test_required_is_the_exact_upgrade_list(self):
        rows = _with(CURRENT, ncollection_core=('installed', '19.0.1.22.0'),
                     ncollection_branding=('installed', '19.0.1.10.0'))
        self.assertEqual(gate.evaluate(rows, None, False, RELEASE)['required'],
                         ['ncollection_branding', 'ncollection_core'])
        self.assertEqual(gate.evaluate(CURRENT, None, False, RELEASE)['required'], [])


@tagged('post_install', '-at_install')
class TestReleaseGateOnThisDatabase(TransactionCase):
    """``read_state`` with plain SQL on a real database, the stale state fabricated in the test transaction."""

    def _state(self):
        return gate.read_state(self.env.cr, self.env.cr.dbname)

    def test_the_test_database_matches_its_code(self):
        result = self._state()
        self.assertEqual(result['state'], gate.READY, result)
        self.assertEqual(result['release_fingerprint'], gate.release_manifest()['fingerprint'])

    def test_the_release_universe_is_ncollection_and_oca_never_odoo_core(self):
        modules = gate.release_manifest()['modules']
        self.assertIn('ncollection_core', modules)
        self.assertIn('queue_job', modules)
        for core in ('base', 'web', 'mail', 'account'):
            self.assertNotIn(core, modules)

    def test_a_module_recorded_behind_its_code_is_upgrade_required(self):
        self.env.cr.execute(
            "UPDATE ir_module_module SET latest_version = '19.0.1.1.0' WHERE name = 'ncollection_core'")
        result = self._state()
        self.assertEqual(result['state'], gate.UPGRADE_REQUIRED)
        self.assertEqual(result['behind'][0][0], 'ncollection_core')

    def test_a_hold_is_read_from_the_parameter_table(self):
        gate.set_hold(self.env.cr, 'upgrade_failed:the migration raised')
        result = self._state()
        self.assertEqual((result['state'], result['hold_reason']), (gate.UPGRADE_FAILED, 'the migration raised'))
        gate.clear_hold(self.env.cr)
        self.assertEqual(self._state()['state'], gate.READY)

    def test_the_provisioning_engine_holds_a_new_database_and_nothing_else_does(self):
        with patch.dict(os.environ, {gate.HOLD_ENV: ''}):
            _hold_for_provisioning(self.env)
        self.assertEqual(self._state()['state'], gate.READY, 'a developer or test install is not held')
        with patch.dict(os.environ, {gate.HOLD_ENV: 'provisioning'}):
            _hold_for_provisioning(self.env)
        result = self._state()
        self.assertEqual(result['state'], gate.PROVISIONING)
        self.assertTrue(result['passable'], 'the engine validates it before opening it')

    def test_the_platform_bearer_is_the_tenants_own_stored_key(self):
        raw = 'release-gate-test-key-0123456789abcdef'
        self.env['ncollection.config.sync.key']._install_key(raw, create_user=True)
        self.assertTrue(gate.service_bearer_ok(self.env.cr, 'Bearer %s' % raw))
        for wrong in ('Bearer %s' % raw[:-1] + 'x', 'Basic %s' % raw, raw, '', 'Bearer short'):
            with self.subTest(header=wrong):
                self.assertFalse(gate.service_bearer_ok(self.env.cr, wrong))

    # ---- the cache: a token, never a time window ----------------------------------------------------------------
    def test_a_cached_ready_never_outlives_a_release_transition(self):
        """READY cached -> a hold / a module entering an upgrade / a version written -> the NEXT read sees it. The
        evaluation is reused only while the database's token is unchanged."""
        db = self.env.cr.dbname
        gate.invalidate(db)
        self.addCleanup(gate.invalidate, db)
        self.assertEqual(gate.workspace_state(db, self.env.cr)['state'], gate.READY)
        with patch.object(gate, 'read_state', wraps=gate.read_state) as spy:
            self.assertEqual(gate.workspace_state(db, self.env.cr)['state'], gate.READY)
            self.assertEqual(spy.call_count, 0, 'an unchanged token reuses the evaluation')
            gate.set_hold(self.env.cr, 'upgrading:release')
            self.assertEqual(gate.workspace_state(db, self.env.cr)['state'], gate.UPGRADING)
            gate.clear_hold(self.env.cr)
            self.assertEqual(gate.workspace_state(db, self.env.cr)['state'], gate.READY)
            self.env.cr.execute("UPDATE ir_module_module SET state = 'to upgrade' WHERE name = 'ncollection_core'")
            self.assertEqual(gate.workspace_state(db, self.env.cr)['state'], gate.UPGRADING)
            self.env.cr.execute("UPDATE ir_module_module SET state = 'installed', latest_version = '19.0.1.1.0' "
                                "WHERE name = 'ncollection_core'")
            self.assertEqual(gate.workspace_state(db, self.env.cr)['state'], gate.UPGRADE_REQUIRED)
            self.assertEqual(spy.call_count, 4, 'every transition re-evaluated, none served from the cache')

    def test_a_schema_gap_holds_until_the_module_rows_change(self):
        db = self.env.cr.dbname
        self.addCleanup(gate._schema_gaps.pop, db, None)
        self.addCleanup(gate.invalidate, db)
        gate.note_schema_report(db, {'missing_tables': [], 'missing_columns': [('res.users', 'x')]}, self.env.cr)
        self.assertEqual(self._state()['state'], gate.BLOCKED)
        self.assertTrue(self._state()['schema_gap'])
        # an -u writes the module rows: the gap found against the old rows no longer applies (the next registry load
        # re-checks the schema and reports again if it is still missing)
        self.env.cr.execute("UPDATE ir_module_module SET write_date = now() AT TIME ZONE 'utc' "
                            "WHERE name = 'ncollection_core'")
        self.assertEqual(self._state()['state'], gate.READY)

    # ---- the operator channel ------------------------------------------------------------------------------------
    def _operator_bearer(self, master, db):
        return 'Bearer %s' % base64.urlsafe_b64encode(
            hmac.new(master.encode(), b'nc-config-sync:' + db.encode(), hashlib.sha256).digest()).decode()

    def test_the_platform_operator_bearer_is_per_database_constant_time_and_platform_only(self):
        db = self.env.cr.dbname
        master = 'release-gate-test-master-0123456789'
        with patch.dict(os.environ, {gate.MASTER_ENV: master}):
            good = self._operator_bearer(master, db)
            self.assertTrue(gate.platform_bearer_ok(db, gate.PLATFORM, good))
            self.assertFalse(gate.platform_bearer_ok(db, gate.TENANT, good), 'a tenant needs its own stored key')
            for wrong in (self._operator_bearer(master, db + 'x'), self._operator_bearer(master + 'x', db),
                          good.replace('Bearer', 'Basic'), good.split(' ')[1], '', 'Bearer '):
                with self.subTest(header=wrong[:20]):
                    self.assertFalse(gate.platform_bearer_ok(db, gate.PLATFORM, wrong))
        with patch.dict(os.environ, {gate.MASTER_ENV: ''}):
            self.assertFalse(gate.platform_bearer_ok(db, gate.PLATFORM, good), 'no master here: nobody is accepted')

    def _call(self, path, method='GET', authorization='', body=''):
        httprequest = SimpleNamespace(headers={'Authorization': authorization} if authorization else {},
                                      get_data=lambda as_text=False: body)
        return gate._release_call(self.env.cr, self.env.cr.dbname, path, method, httprequest)

    def test_the_status_call_answers_everything_an_operator_needs_and_nothing_to_anyone_else(self):
        db = self.env.cr.dbname
        role = self._state()['role']
        master = 'release-gate-test-master-0123456789'
        with patch.dict(os.environ, {gate.MASTER_ENV: master}):
            bearer = self._operator_bearer(master, db)
            self.assertEqual(self._call(gate.STATUS_PATH)[0], 404)
            self.assertEqual(self._call(gate.STATUS_PATH, authorization='Bearer nope-nope-nope')[0], 404)
            code, body = self._call(gate.STATUS_PATH, authorization=bearer)
            if role == gate.TENANT:
                self.assertEqual(code, 404, 'a tenant does not accept the platform operator bearer')
                return
            self.assertEqual(code, 200)
            for key in ('state', 'role', 'required', 'behind', 'release_fingerprint', 'db_fingerprint', 'last_ok',
                        'last_error', 'checked_at', 'hold', 'compatible'):
                self.assertIn(key, body)
            self.assertEqual(body['role'], gate.PLATFORM)
            self.assertNotIn(master, json.dumps(body))
            code, body = self._call(gate.HOLD_PATH, 'POST', bearer, json.dumps(
                {'hold': 'upgrade_failed', 'reason': 'the rehearsal failed'}))
            self.assertEqual((code, body['state']), (200, gate.UPGRADE_FAILED))
            self.assertIn('the rehearsal failed', body['last_error'])
            code, body = self._call(gate.OPEN_PATH, 'POST', bearer, '{}')
            self.assertEqual((code, body['state']), (200, gate.READY))
            self.assertTrue(body['last_ok'].endswith('release=%s' % gate.release_manifest()['fingerprint']))
            self.env.cr.execute("UPDATE ir_module_module SET latest_version = '19.0.1.1.0' "
                                "WHERE name = 'ncollection_core'")
            code, body = self._call(gate.OPEN_PATH, 'POST', bearer, '{}')
            self.assertEqual((code, body['required']), (409, ['ncollection_core']), 'never opened while behind')


@tagged('post_install', '-at_install')
class TestReleaseGateResponses(TransactionCase):

    def _request(self, path, accept='text/html'):
        return SimpleNamespace(path=path, headers={'Accept': accept})

    def test_the_maintenance_page_is_a_503_with_the_system_identity_and_no_internals(self):
        stale = gate.evaluate(_with(CURRENT, ncollection_core=('installed', '19.0.1.1.0')), None, False, RELEASE)
        response = gate.maintenance_response(self.env.cr.dbname, stale, self._request('/web/login'))
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers[gate.STATE_HEADER], gate.UPGRADE_REQUIRED)
        self.assertEqual(response.headers['Retry-After'], '120')
        self.assertIn('This workspace is being updated', body)
        self.assertIn('يجري تحديث مساحة العمل هذه', body)
        self.assertIn(self.env['ncollection.product.branding'].nc_brand()['name'], body, 'System Branding name')
        for internal in ('Traceback', 'Internal Server Error', 'overloaded', 'ncollection_core', '19.0.1.1.0',
                         self.env.cr.dbname, 'psycopg2'):
            with self.subTest(leak=internal):
                self.assertNotIn(internal, body)

    def test_a_json_client_gets_a_json_503_without_internals(self):
        stale = gate.evaluate(_with(CURRENT, ncollection_core=('installed', '19.0.1.1.0')), None, False, RELEASE)
        response = gate.maintenance_response(self.env.cr.dbname, stale, self._request('/json/2/res.partner/search'))
        self.assertEqual(response.status_code, 503)
        self.assertIn('application/json', response.headers['Content-Type'])
        self.assertNotIn('ncollection_core', response.get_data(as_text=True))

    def test_the_gate_wraps_the_dispatcher_once_and_keeps_the_original(self):
        self.assertTrue(gate.installed(), 'ncollection_core is server-wide: the gate must be in place')
        wrapped = http.Request._serve_db
        gate.install()
        self.assertIs(http.Request._serve_db, wrapped, 'installing twice must not wrap twice')
        self.assertFalse(getattr(wrapped.__wrapped__, '_nc_release_gate', False))

    def test_the_drift_check_feeds_the_gate(self):
        db = self.env.cr.dbname
        try:
            gate.note_schema_report(db, {'missing_tables': [], 'missing_columns': [('res.users', 'x')]}, self.env.cr)
            self.assertEqual(self._fresh(db)['state'], gate.BLOCKED)
        finally:
            gate.note_schema_report(db, {'missing_tables': [], 'missing_columns': []}, self.env.cr)
        self.assertEqual(self._fresh(db)['state'], gate.READY)

    def _fresh(self, db):
        return gate.read_state(self.env.cr, db)

    # ---- schema errors degrade to the 503; nothing else is caught ------------------------------------------------
    def _serve(self, raising):
        """The gate's real decision path (``_gate``: what ``gated_serve_db`` runs outside test mode) on THIS database,
        with an original ``_serve_db`` that raises ``raising``."""
        db = self.env.cr.dbname
        self.addCleanup(gate._schema_gaps.pop, db, None)
        self.addCleanup(gate.invalidate, db)
        self.addCleanup(gate._last_logged.pop, db, None)

        def original(_request):
            raise raising

        httprequest = SimpleNamespace(path='/web/login', headers={'Accept': 'text/html'})
        request = SimpleNamespace(db=db, httprequest=httprequest)
        return gate._gate(request, original)

    def _undefined_column(self):
        return psycopg2.errors.UndefinedColumn('column ncollection_product_branding.browser_title does not exist\n'
                                               'LINE 1: SELECT secret_customer_value')

    def test_an_undefined_column_from_a_ready_database_is_a_503_not_a_500(self):
        with self.assertLogs(gate.__name__, level='ERROR') as logs:
            response = self._serve(self._undefined_column())
        self.assertEqual((response.status_code, response.headers[gate.STATE_HEADER]), (503, gate.BLOCKED))
        body = response.get_data(as_text=True)
        self.assertIn('This workspace is being updated', body)
        for leak in ('Traceback', 'browser_title', 'psycopg2', 'secret_customer_value'):
            self.assertNotIn(leak, body)
        line = logs.output[0]
        self.assertIn('intercepted a schema error', line)
        self.assertIn('UndefinedColumn: column ncollection_product_branding.browser_title does not exist', line)
        self.assertNotIn('secret_customer_value', line.splitlines()[0], 'never the statement, which carries values')
        db = self.env.cr.dbname
        self.assertEqual(gate.workspace_state(db)['state'], gate.BLOCKED, 'held until its module rows change')

    def test_the_registry_and_qweb_wrappers_are_unwrapped_and_nothing_else(self):
        def wrapped(wrapper, cause):
            try:
                raise cause
            except Exception as exc:  # noqa: BLE001
                try:
                    raise wrapper from exc
                except Exception as outer:  # noqa: BLE001
                    return outer

        qweb = QWebError(QWebErrorInfo('boom', 'web.login_layout', None, None, None, [], ''))
        for exc in (wrapped(http.RegistryError('Cannot get registry'), psycopg2.errors.UndefinedTable('relation')),
                    wrapped(qweb, self._undefined_column())):
            with self.subTest(wrapper=type(exc).__name__), self.assertLogs(gate.__name__, level='ERROR'):
                self.assertEqual(self._serve(exc).status_code, 503)
            gate._schema_gaps.pop(self.env.cr.dbname, None)
        other_qweb = QWebError(QWebErrorInfo('x', None, None, None, None, [], ''))
        for exc in (ValueError('an application bug'), wrapped(other_qweb, KeyError('k')),
                    psycopg2.errors.UniqueViolation('duplicate key')):
            with self.subTest(other=type(exc).__name__), self.assertRaises(type(exc)):
                self._serve(exc)
        self.assertNotIn(self.env.cr.dbname, gate._schema_gaps, 'an application error never blocks a database')

    # ---- the reason reaches Odoo's configured logging ------------------------------------------------------------
    def test_the_gate_reason_reaches_odoo_s_configured_log_handlers(self):
        """The gate's logger is Odoo's, enabled and propagating to the ROOT handlers ``odoo.netsvc`` installed (the
        same path the server's log uses), and a blocking decision carries the database, role, state transition,
        exact modules and fingerprints. The live server log is checked by verify_platform_release.sh."""
        logger = logging.getLogger(gate.__name__)
        self.assertIs(gate._logger, logger)
        self.assertEqual(logger.name, 'odoo.addons.ncollection_core.release_gate')
        chain, node = [], logger
        while node is not None:
            chain.append(node)
            node = node.parent if node.propagate else None
        self.assertIs(chain[-1], logging.getLogger(), 'propagates all the way to the root logger')
        self.assertFalse(any(node.disabled for node in chain))
        self.assertTrue(logger.isEnabledFor(logging.WARNING))
        odoo_handlers = list(logging.getLogger().handlers)
        self.assertTrue(odoo_handlers, 'odoo.netsvc.init_logger installs the root handlers')
        captured = []

        class _Capture(logging.Handler):
            def emit(self, record):
                captured.append(record)

        capture = _Capture()
        logging.getLogger().addHandler(capture)
        probe = 'nclogprobe'
        stale = gate.evaluate(_with(CURRENT, ncollection_core=('installed', '19.0.1.1.0')) + [
            (gate.PLATFORM_MODULE, 'installed', '19.0.6.19.0')], None, False, RELEASE)
        try:
            gate._log_decision(probe, stale, '/web/login')
            gate._log_decision(probe, stale, '/web/login')
        finally:
            logging.getLogger().removeHandler(capture)
            gate._last_logged.pop(probe, None)
        records = [record for record in captured if record.name == gate.__name__]
        self.assertEqual(len(records), 1, 'once per change of decision, never once per request')
        record = records[0]
        self.assertEqual(record.levelno, logging.WARNING)
        message = record.getMessage()
        for part in ('db=nclogprobe', 'role=platform', 'state=-->UPGRADE_REQUIRED', 'required=ncollection_core ',
                     'behind=ncollection_core 19.0.1.1.0<19.0.1.23.0', 'release=ffffffffffff'):
            self.assertIn(part, message)
        for handler in odoo_handlers:
            with self.subTest(handler=type(handler).__name__):
                self.assertLessEqual(handler.level, record.levelno)
                self.assertTrue(handler.filter(record))

    # ---- the seam -------------------------------------------------------------------------------------------------
    def test_liveness_never_loads_the_registry_of_a_database_behind_the_code(self):
        """A server holding ONE database resolves every request to it (monodb), ``/web/health`` included: passing
        that to the original ``_serve_db`` loaded the registry of a platform copy behind the code
        (verify_platform_release.sh, step 4). Liveness is Odoo's no-database dispatch, decided before the state
        is even read."""
        db = self.env.cr.dbname
        self.addCleanup(gate.invalidate, db)
        served = []

        def original(_request):
            raise AssertionError("/web/health reached the registry")

        request = SimpleNamespace(db=db, httprequest=SimpleNamespace(path='/web/health', headers={}))
        # Odoo's dispatcher reads ``request.db`` to pick a registry: a nodb dispatch with it set is an HTTP 500.
        request._serve_nodb = lambda: served.append(request.db) or 'liveness'
        with patch.object(gate, 'workspace_state', side_effect=AssertionError('liveness read the release state')):
            self.assertEqual(gate._gate(request, original), 'liveness')
        self.assertEqual(served, [None])

    def test_the_seam_is_the_one_this_gate_was_written_for(self):
        """Fails on an Odoo update that renames ``_serve_db``, changes its arguments, moves the registry load out of it
        or stops ``Application`` from calling it - instead of a gate that silently never runs."""
        self.assertIsNone(gate.seam_problem())
        self.assertTrue(gate.installed())

    def test_a_moved_seam_is_refused_loudly_and_left_alone(self):
        def without_registry(self):
            return None

        def with_arguments(self, rule):
            return rule

        self.assertIn('no longer loads the registry', gate.seam_problem(without_registry))
        self.assertIn('takes self, rule', gate.seam_problem(with_arguments))
        with patch.object(http.Request, '_serve_db', without_registry), \
                self.assertLogs(gate.__name__, level='CRITICAL') as logs:
            gate.install()
            self.assertIs(http.Request._serve_db, without_registry, 'never wrapped')
        self.assertIn('Release gate NOT INSTALLED', logs.output[0])
