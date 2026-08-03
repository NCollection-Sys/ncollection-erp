# -*- coding: utf-8 -*-
"""Tenant backup manager (P2-T05) — CI-safe unit tests.

Cover the platform-side logic without running real pg_dump/pg_restore (mocked
subprocess): success/failure recording + alert, the nightly per-tenant fan-out,
the retention pyramid, and the restore-wizard safety guard. The real
backup→restore-including-attachments round-trip is proven by
scripts/backup/verify_tenant_backup.sh (evidence in the PR).
"""
from unittest.mock import MagicMock, patch

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged

BACKUP = 'odoo.addons.ncollection_saas.models.backup'


@tagged('post_install', '-at_install')
class TestBackup(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ir.config_parameter'].sudo().set_param(
            'ncollection_saas.provisioning_quota_per_hour', '0')
        cls.plan = cls.env['ncollection.subscription.plan'].create({
            'name': 'Growth', 'code': 'BKPGROWTH', 'allowed_module_names': 'crm'})
        cls.Backup = cls.env['ncollection.backup']

    def _tenant(self, **kw):
        vals = {'company_name': 'Acme', 'plan_id': self.plan.id, 'status': 'active',
                'database_status': 'ready', 'database_name': 'acme'}
        vals.update(kw)
        return self.env['ncollection.tenant'].create(vals)

    def _mock_run(self, stdout='', returncode=0, stderr=''):
        m = MagicMock()
        m.stdout, m.returncode, m.stderr = stdout, returncode, stderr
        return m

    # ---- engine: success / failure --------------------------------------

    def test_run_backup_records_success(self):
        rec = self.Backup.create({'tenant_id': self._tenant().id})
        out = "RESULT_PATH=/var/lib/odoo/backups/acme/x.tar.enc\nRESULT_BYTES=2048\n"
        with patch(BACKUP + '.subprocess.run', return_value=self._mock_run(out)):
            rec.run_backup()
        self.assertEqual(rec.status, 'done')
        self.assertEqual(rec.file_path, '/var/lib/odoo/backups/acme/x.tar.enc')
        self.assertEqual(rec.file_size, 2048)

    def test_run_backup_records_failure_and_alerts(self):
        rec = self.Backup.create({'tenant_id': self._tenant().id})
        activities_before = len(rec.activity_ids)
        with patch(BACKUP + '.subprocess.run',
                   return_value=self._mock_run(returncode=1, stderr='pg_dump boom')):
            rec.run_backup()
        self.assertEqual(rec.status, 'failed')
        self.assertIn('boom', rec.error_log)
        self.assertEqual(len(rec.activity_ids), activities_before + 1,
                         "a failed backup schedules an investigation activity")

    # ---- nightly fan-out + typing ---------------------------------------

    def test_nightly_backs_up_each_ready_tenant(self):
        ready = self._tenant(database_name='bkprdy')
        self._tenant(database_name=False, database_status='not_provisioned')
        with patch.object(type(self.Backup), '_enqueue'):  # don't touch the queue
            self.Backup._cron_nightly_backups()
        self.assertEqual(self.Backup.search_count([('tenant_id', '=', ready.id)]), 1)

    def test_backup_type_for_today_is_valid(self):
        self.assertIn(self.Backup._backup_type_for_today(),
                      ('daily', 'weekly', 'monthly'))

    # ---- retention pyramid ----------------------------------------------

    def test_prune_keeps_only_retention_window(self):
        tenant = self._tenant(database_name='ret')
        # 10 done daily backups; retention keeps 7.
        for _ in range(10):
            self.Backup.create({
                'tenant_id': tenant.id, 'backup_type': 'daily', 'status': 'done'})
        self.Backup._cron_prune()
        self.assertEqual(
            self.Backup.search_count([
                ('tenant_id', '=', tenant.id), ('backup_type', '=', 'daily')]),
            7, "prune keeps 7 daily")

    # ---- restore wizard safety ------------------------------------------

    def test_wizard_refuses_live_tenant_db(self):
        tenant = self._tenant(database_name='live1')
        backup = self.Backup.create({
            'tenant_id': tenant.id, 'status': 'done',
            'file_path': '/tmp/x.tar.enc'})
        wiz = self.env['ncollection.backup.restore.wizard'].create({
            'backup_id': backup.id, 'target_db': 'live1'})  # a LIVE tenant DB
        with self.assertRaises(UserError):
            wiz.action_restore()

    def test_restore_drill_skips_live_tenant_collision(self):
        """ISO-2 (P3-T12): the UNATTENDED monthly drill dropdb+createdb's its
        scratch target, so it must refuse to clobber a live tenant DB — the same
        guard the interactive wizard has."""
        src = self._tenant(database_name='victim')
        backup = self.Backup.create({
            'tenant_id': src.id, 'status': 'done', 'file_path': '/tmp/x.tar.enc'})
        # A tenant whose db name collides with the drill's scratch target. It is
        # not_provisioned because the drill_<x> prefix contains an underscore (the
        # ISO-1 grammar guard forbids underscores at ready) — the collision guard
        # searches by name regardless of status, so this still exercises it.
        self._tenant(database_name='drill_victim', database_status='not_provisioned')
        with patch.object(type(backup), 'restore_to') as restore, \
                patch.object(type(backup), '_alert_failure') as alert:
            self.Backup._cron_restore_drill()
        restore.assert_not_called()          # never touched the live tenant DB
        alert.assert_called_once()           # surfaced the collision instead

    # -- restore target binding (#275) --------------------------------------
    #
    # restore_to drives pg_terminate_backend -> dropdb -> createdb. Every
    # CALLER guarded it and the method itself did not, so the guards were
    # bypassable simply by not being a caller (raw call_kw / json2 reach it
    # directly). These tests pin the rule to the METHOD.

    def _done_backup(self, tenant):
        return self.Backup.create({
            'tenant_id': tenant.id, 'backup_type': 'daily',
            'status': 'done', 'file_path': '/tmp/x.dump'})

    def test_restoring_over_ANOTHER_live_tenant_is_refused(self):
        """The hole: one RPC drops a live tenant and serves someone else's
        snapshot under its database name."""
        victim = self._tenant(company_name='Victim', database_name='victimco')
        backup = self._done_backup(self._tenant())

        with patch(BACKUP + '.subprocess.run') as run:
            with self.assertRaises(ValidationError):
                backup.restore_to(victim.database_name)
            run.assert_not_called()

    def test_in_place_restore_of_its_OWN_tenant_is_allowed(self):
        """#244's rollback restores a tenant's snapshot over that same tenant's
        live database. A guard that blocked this would break recovery — worse
        than the hole it closes."""
        tenant = self._tenant()
        backup = self._done_backup(tenant)

        with patch(BACKUP + '.subprocess.run',
                   return_value=self._mock_run()) as run:
            self.assertEqual(backup.restore_to(tenant.database_name),
                             tenant.database_name)
            run.assert_called()

    def test_a_scratch_name_with_an_underscore_is_allowed(self):
        """Both shipped generators emit underscores — `drill_%s` and
        `restore_%s`. The strict tenant rule forbids them, so applying it to
        scratch targets would have broken the nightly drill and the wizard.

        The underscore is also load-bearing: db_filter = ^%d$ means an
        underscored name is unreachable by subdomain, so a restored copy of
        another tenant's data stays off the network.
        """
        backup = self._done_backup(self._tenant())
        for target in ('drill_acme', 'restore_acme'):
            with patch(BACKUP + '.subprocess.run',
                       return_value=self._mock_run()) as run:
                self.assertEqual(backup.restore_to(target), target)
                run.assert_called()

    def test_reserved_and_platform_databases_are_refused(self):
        backup = self._done_backup(self._tenant())
        for target in ('postgres', 'template1', 'admin', self.env.cr.dbname):
            with patch(BACKUP + '.subprocess.run') as run:
                with self.assertRaises(ValidationError):
                    backup.restore_to(target)
                run.assert_not_called()

    def test_injection_shaped_targets_are_refused(self):
        """The target reaches a bash script argument and a dropdb."""
        backup = self._done_backup(self._tenant())
        for target in ('acme; rm -rf /', 'acme db', '../etc', 'ACME', '', 'a'):
            with patch(BACKUP + '.subprocess.run') as run:
                with self.assertRaises(ValidationError):
                    backup.restore_to(target)
                run.assert_not_called()

    def test_wizard_enqueues_restore_to_scratch(self):
        tenant = self._tenant(database_name='live2')
        backup = self.Backup.create({
            'tenant_id': tenant.id, 'status': 'done',
            'file_path': '/tmp/x.tar.enc'})
        wiz = self.env['ncollection.backup.restore.wizard'].create({
            'backup_id': backup.id, 'target_db': 'restore_live2'})
        with patch.object(type(backup), 'with_delay',
                          return_value=MagicMock()) as delayed:
            res = wiz.action_restore()
        delayed.assert_called_once()
        self.assertEqual(res['params']['type'], 'success')
