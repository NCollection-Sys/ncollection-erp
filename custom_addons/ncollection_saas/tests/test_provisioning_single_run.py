# -*- coding: utf-8 -*-
"""Production job 6: two runs of ONE provisioning job overlapped (a re-queue while the first was still building). The
'running' status of the first is uncommitted, so the second passed the status guard, "cleared its own leftover" and
dropped the database the first was creating. A run now holds the job's row lock; a concurrent one steps aside.
"""
from unittest.mock import patch

import psycopg2.errors

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestProvisioningSingleRun(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        plan = cls.env['ncollection.subscription.plan'].create({
            'name': 'Single run', 'code': 'SRUN', 'allowed_module_names': 'crm', 'max_users': 3})
        tenant = cls.env['ncollection.tenant'].create({
            'company_name': 'Single Run Co', 'database_name': 'singlerun', 'email': 'o@singlerun.example',
            'plan_id': plan.id, 'status': 'trial', 'registration_state': 'approved'})
        cls.job = cls.env['ncollection.provisioning.job'].create({
            'tenant_id': tenant.id, 'database_name': 'singlerun', 'status': 'failed'})

    def test_the_lock_is_taken_when_free(self):
        self.assertTrue(self.job._nc_lock_for_run())

    def _contended(self, error):
        real = type(self.env.cr).execute

        def execute(cr, query, params=None, *args, **kwargs):
            if 'FOR UPDATE NOWAIT' in str(query):
                raise error
            return real(cr, query, params, *args, **kwargs)
        return patch.object(type(self.env.cr), 'execute', execute)

    def test_a_concurrent_run_touches_nothing(self):
        for error in (psycopg2.errors.LockNotAvailable(), psycopg2.errors.SerializationFailure()):
            with self.subTest(error=type(error).__name__), \
                    patch.object(type(self.job), '_drop_database') as drop, \
                    patch.object(type(self.job), '_run_odoo_init') as init, \
                    patch.object(type(self.job), '_nc_db_is_owned', return_value=True), \
                    patch.object(type(self.job), '_safe_to_drop', return_value=True), \
                    self._contended(error):
                self.assertTrue(self.job.run_provisioning())
                drop.assert_not_called()          # never the other run's database
                init.assert_not_called()
                self.assertEqual(self.job.status, 'failed', "the job is left exactly as it was")
