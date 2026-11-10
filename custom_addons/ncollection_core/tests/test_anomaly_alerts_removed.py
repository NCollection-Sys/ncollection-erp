# -*- coding: utf-8 -*-
"""Anomaly Alerts are gone everywhere, history is archived before the table goes, and nothing unrelated went with them."""

import importlib.util
import os

from odoo.tests import TransactionCase, tagged

_MIG = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'migrations', '19.0.1.29.0')


def _load(name):
    spec = importlib.util.spec_from_file_location(
        'nc_mig_' + name.replace('-', '_'), os.path.join(_MIG, name + '.py')
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@tagged('post_install', '-at_install')
class TestAnomalyAlertsRemoved(TransactionCase):
    def test_nothing_of_the_feature_is_left_in_a_database(self):
        self.assertNotIn('ncollection.alert', self.env)
        names = (
            'cron_detect_anomalies',
            'cron_send_alert_digest',
            'menu_ncollection_alert',
            'action_ncollection_alert',
        )
        self.assertFalse(
            self.env['ir.model.data'].search([('module', '=', 'ncollection_core'), ('name', 'in', names)])
        )
        self.assertFalse(
            self.env['ir.cron'].with_context(active_test=False).search([('cron_name', 'ilike', 'anomal')])
        )
        self.assertFalse(
            self.env['ir.ui.menu'].with_context(active_test=False).search([('name', 'ilike', 'anomaly')])
        )
        self.env.cr.execute("SELECT to_regclass('ncollection_alert')")
        self.assertIsNone(self.env.cr.fetchone()[0])
        self.assertFalse(self.env['ir.model'].search([('model', '=', 'ncollection.alert')]))

    def test_unrelated_machinery_survives(self):
        # the scheduler identity (used by the platform's administration service) and the release gate are not part of the feature
        self.assertTrue(self.env.ref('ncollection_core.group_cron_service', raise_if_not_found=False))
        self.assertTrue(self.env.ref('ncollection_core.user_cron_service', raise_if_not_found=False))
        from odoo.addons.ncollection_core import release_gate  # noqa: F401 - importable and loaded

    def test_the_migration_archives_history_before_dropping_the_table_and_is_idempotent(self):
        cr = self.env.cr
        cr.execute("CREATE TABLE ncollection_alert (id serial PRIMARY KEY, name varchar)")
        cr.execute("INSERT INTO ncollection_alert (name) VALUES ('old alert 1'), ('old alert 2')")
        pre, post = _load('pre-migrate'), _load('post-migrate')
        pre.migrate(cr, '19.0.1.28.0')
        cr.execute("SELECT count(*) FROM ncollection_alert_archive")
        self.assertEqual(cr.fetchone()[0], 2)
        post.migrate(cr, '19.0.1.28.0')
        cr.execute("SELECT to_regclass('ncollection_alert')")
        self.assertIsNone(cr.fetchone()[0])  # dropped only after the archive exists
        # second run: no table, nothing copied, the archive is left alone
        pre.migrate(cr, '19.0.1.28.0')
        post.migrate(cr, '19.0.1.28.0')
        cr.execute("SELECT count(*), min(name) FROM ncollection_alert_archive")
        self.assertEqual(tuple(cr.fetchone()), (2, 'old alert 1'))

    def test_a_table_with_history_is_never_dropped_without_an_archive(self):
        cr = self.env.cr
        cr.execute("CREATE TABLE ncollection_alert (id serial PRIMARY KEY, name varchar)")
        cr.execute("INSERT INTO ncollection_alert (name) VALUES ('only copy')")
        _load('post-migrate').migrate(cr, '19.0.1.28.0')
        cr.execute("SELECT to_regclass('ncollection_alert')")
        self.assertIsNotNone(cr.fetchone()[0])  # kept: the rows are not archived
