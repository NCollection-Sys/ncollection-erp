# -*- coding: utf-8 -*-
"""Code-versus-database drift (R-037): the condition that turns a valid code change into HTTP 500.

The failure being pinned: newer code declares a stored field, the database was never upgraded, and the first request
that reads the model dies with ``UndefinedColumn``. Each test fabricates exactly that state INSIDE the test
transaction (PostgreSQL DDL is transactional, so nothing outlives the test) and asserts the detector sees it, names it
and that the registry-load hook reports it without ever raising.
"""
import logging
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

from odoo.addons.ncollection_core.models import schema_drift

LOGGER = 'odoo.addons.ncollection_core.models.schema_drift'


@tagged('post_install', '-at_install')
class TestSchemaDrift(TransactionCase):

    def _installed_version(self, module, version):
        self.env.cr.execute(
            "UPDATE ir_module_module SET latest_version = %s WHERE name = %s", (version, module))

    def _drop(self, table, column):
        self.env.cr.execute('ALTER TABLE "%s" DROP COLUMN "%s"' % (table, column))

    def test_a_healthy_database_reports_no_drift_so_the_check_has_no_false_positives(self):
        report = schema_drift.drift_report(self.env)
        self.assertFalse(report['drift'], schema_drift.describe(report)[:10])
        self.assertEqual((report['behind'], report['missing_tables'], report['missing_columns']), ([], [], []))

    def test_a_column_the_code_declares_and_the_database_lacks_is_named(self):
        """The reported incident: res.users.nc_must_change_password added by newer code, never migrated."""
        self._drop('res_users', 'nc_must_change_password')
        report = schema_drift.drift_report(self.env)
        self.assertTrue(report['drift'])
        self.assertIn(('res.users', 'nc_must_change_password'), report['missing_columns'])
        self.assertIn('field res.users.nc_must_change_password: column missing', schema_drift.describe(report))
        self.assertEqual(report['behind'], [], 'a gap at an UNCHANGED version is caught: version alone is not enough')

    def test_that_missing_column_really_is_what_makes_the_request_fail(self):
        """The detector is not guessing: with the column gone, reading the model raises the error behind the 500."""
        self._drop('res_users', 'nc_must_change_password')
        self.env.invalidate_all()
        with self.assertRaises(Exception) as caught:
            self.env['res.users'].browse(2).read(['login', 'nc_must_change_password'])
        self.assertIn('nc_must_change_password', str(caught.exception))

    def test_a_missing_table_is_named(self):
        self.env.cr.execute('ALTER TABLE ncollection_product_branding RENAME TO ncollection_product_branding_moved')
        report = schema_drift.drift_report(self.env)
        self.assertIn('ncollection.product.branding', report['missing_tables'])
        self.assertIn('model ncollection.product.branding: table missing', schema_drift.describe(report))

    def test_a_module_whose_code_is_newer_than_the_database_is_reported_behind(self):
        disk = schema_drift._disk_version('ncollection_core')
        self._installed_version('ncollection_core', '19.0.1.0.0')
        report = schema_drift.drift_report(self.env)
        self.assertIn(('ncollection_core', '19.0.1.0.0', disk), report['behind'])
        self.assertIn('module ncollection_core: database 19.0.1.0.0 < code %s' % disk, schema_drift.describe(report))
        self.assertEqual({name for name, _i, _d in report['behind']}, {'ncollection_core'}, 'only that module')

    def test_a_database_newer_than_the_code_is_not_reported(self):
        self._installed_version('ncollection_core', '99.0.9.9.9')
        self.assertEqual(schema_drift.version_drift(self.env), [])

    def test_versions_are_compared_as_versions_not_as_text(self):
        self._installed_version('ncollection_branding', '19.0.1.9.0')
        disk = schema_drift._disk_version('ncollection_branding')   # 19.0.1.11.0: 'less' than 19.0.1.9.0 as text
        self.assertIn(('ncollection_branding', '19.0.1.9.0', disk), schema_drift.version_drift(self.env))

    # ----------------------------------------------------------------- the registry-load hook
    def test_the_registry_load_hook_logs_one_error_that_names_the_cause_and_the_repair(self):
        self._drop('res_users', 'nc_must_change_password')
        self._installed_version('ncollection_core', '19.0.1.0.0')
        with self.assertLogs(LOGGER, 'ERROR') as logs:
            self.env['ir.module.module']._register_hook()
        self.assertEqual(len(logs.output), 1, 'one line, not a flood')
        line = logs.output[0]
        self.assertIn('SCHEMA DRIFT', line)
        self.assertIn('HTTP 500', line)
        self.assertIn('module ncollection_core', line)
        self.assertIn('res.users.nc_must_change_password', line)
        self.assertIn('ONLY the affected modules', line)

    def test_the_hook_is_silent_on_a_consistent_database(self):
        with self.assertNoLogs(LOGGER, 'ERROR'):
            self.env['ir.module.module']._register_hook()

    def test_the_hook_can_never_break_the_registry_it_diagnoses(self):
        with patch.object(schema_drift, 'drift_report', side_effect=RuntimeError('boom')):
            with self.assertLogs(LOGGER, logging.WARNING) as logs:
                self.env['ir.module.module']._register_hook()   # must return, not raise
        self.assertTrue(any('could not run' in line for line in logs.output))

    def test_a_failing_drift_query_does_not_leave_the_registry_transaction_aborted(self):
        """Catching the exception is not enough: a failed SQL statement aborts the transaction, and the rest of the
        registry load (other hooks, flush_all) would then fail on ``InFailedSqlTransaction``."""
        def failing_report(env):
            env.cr.execute("SELECT nc_no_such_column FROM res_users LIMIT 1")
        with patch.object(schema_drift, 'drift_report', side_effect=failing_report), mute_logger('odoo.sql_db'):
            with self.assertLogs(LOGGER, logging.WARNING) as logs:
                self.env['ir.module.module']._register_hook()
        self.assertTrue(any('could not run' in line for line in logs.output), 'the failure is still reported')
        self.env.cr.execute("SELECT 1")
        self.assertEqual(self.env.cr.fetchone(), (1,), 'the surrounding transaction is still usable')
        self.env['res.users'].search([], limit=1)   # and the ORM with it

    def test_a_long_report_is_summarised_in_the_log_but_complete_in_the_report(self):
        for column in ('nc_must_change_password',):
            self._drop('res_users', column)
        report = {'database': 'x', 'behind': [('m%d' % i, '1', '2') for i in range(40)], 'missing_tables': [],
                  'missing_columns': [], 'drift': True}
        with patch.object(schema_drift, 'drift_report', return_value=report):
            with self.assertLogs(LOGGER, 'ERROR') as logs:
                self.env['ir.module.module']._register_hook()
        self.assertIn('; ...', logs.output[0])
        self.assertEqual(len(schema_drift.describe(report)), 40, 'nothing is lost, only the log line is shortened')
