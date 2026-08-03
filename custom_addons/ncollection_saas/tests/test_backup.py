# -*- coding: utf-8 -*-
"""Tenant backup manager (P2-T05) — CI-safe unit tests.

Cover the platform-side logic without running real pg_dump/pg_restore (mocked
subprocess): success/failure recording + alert, the nightly per-tenant fan-out,
the retention pyramid, and the restore-wizard safety guard. The real
backup→restore-including-attachments round-trip is proven by
scripts/backup/verify_tenant_backup.sh (evidence in the PR).
"""
import os
import shutil
import tempfile
from unittest.mock import MagicMock, patch

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged

BACKUP = 'odoo.addons.ncollection_saas.models.backup'
TENANT = 'odoo.addons.ncollection_saas.models.tenant'


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
            'file_path': '/var/lib/odoo/backups/acme/x.tar.enc'})
        wiz = self.env['ncollection.backup.restore.wizard'].create({
            'backup_id': backup.id, 'target_db': 'live1'})  # a LIVE tenant DB
        with self.assertRaises(UserError):
            wiz.action_restore()

    def _drill_backup(self, db='acme'):
        src = self._tenant(database_name=db)
        return self.Backup.create({
            'tenant_id': src.id, 'status': 'done',
            'file_path': '/var/lib/odoo/backups/%s/x.tar.enc' % db})

    def test_the_drill_drops_its_scratch_database(self):
        """It never did, and that left a full, reachable copy of tenant data
        under a guessable name (#287). The drill's only job is proving the
        backup restores — tenant_restore.sh decides that by counting tables —
        so the database has answered its question by the time restore_to
        returns."""
        self._drill_backup()
        dropped = []
        Mixin = type(self.env['ncollection.saas.subprocess.mixin'])
        with patch.object(type(self.Backup), 'restore_to',
                          return_value='drill_acme'), \
                patch.object(Mixin, '_drop_database',
                             side_effect=lambda db: dropped.append(db)):
            self.Backup._cron_restore_drill()
        self.assertEqual(dropped, ['drill_acme'])

    def test_a_FAILED_drill_still_drops_its_database(self):
        """A half-restored copy is no less sensitive than a complete one, so
        the drop lives in `finally`. Dropping costs diagnostic material —
        a conscious trade, since the schema check already failed loudly and
        the error is on the record."""
        self._drill_backup()
        dropped = []
        Mixin = type(self.env['ncollection.saas.subprocess.mixin'])
        with patch.object(type(self.Backup), 'restore_to',
                          side_effect=RuntimeError('pg_restore blew up')), \
                patch.object(type(self.Backup), '_alert_failure'), \
                patch.object(Mixin, '_drop_database',
                             side_effect=lambda db: dropped.append(db)):
            self.Backup._cron_restore_drill()
        self.assertEqual(dropped, ['drill_acme'],
                         "a failed drill leaked its scratch database")

    def test_a_failing_DROP_is_LOUD_and_does_not_mask_the_drill_result(self):
        """Cleanup must never become the verdict — and must never be silent.

        The first version of this test asserted only that message_ids grew.
        Review deleted the ERROR log entirely and it still passed, because the
        chatter message is posted either way. The monitoring story (P2-T10
        watches ERROR lines) rested on a line nothing checked.

        It also asserts the CHATTER tells the truth. The first implementation
        posted "then dropped" before the drop ran, so a failed cleanup left
        the audit trail claiming the opposite of what happened — the leftover
        reachable copy of tenant data hidden by the very record meant to
        surface it.
        """
        backup = self._drill_backup()
        Mixin = type(self.env['ncollection.saas.subprocess.mixin'])
        with patch.object(type(self.Backup), 'restore_to',
                          return_value='drill_acme'), \
                patch.object(Mixin, '_drop_database',
                             side_effect=OSError('postgres unreachable')), \
                self.assertLogs(BACKUP, level='ERROR') as caught:
            self.assertTrue(self.Backup._cron_restore_drill())

        joined = '\n'.join(caught.output)
        self.assertIn('drill_acme', joined,
                      "the ERROR line must name the database left behind")

        backup.invalidate_recordset()
        body = backup.message_ids[0].body or ''
        self.assertIn('NOT REMOVED', body,
                      "chatter claimed the scratch copy was gone when it "
                      "was not — the audit trail must not lie")

    def test_a_successful_drop_is_reported_as_such(self):
        """The other half: when it DOES get dropped, say so, so an operator
        can tell the two apart without reading logs."""
        backup = self._drill_backup()
        Mixin = type(self.env['ncollection.saas.subprocess.mixin'])
        with patch.object(type(self.Backup), 'restore_to',
                          return_value='drill_acme'), \
                patch.object(Mixin, '_drop_database'):
            self.assertTrue(self.Backup._cron_restore_drill())
        backup.invalidate_recordset()
        body = backup.message_ids[0].body or ''
        self.assertIn('scratch copy removed', body)
        self.assertNotIn('NOT REMOVED', body)

    def test_the_drop_uses_the_scratch_name_rule_not_the_strict_one(self):
        """`drill_<db>` contains an underscore. _assert_safe_db_name forbids
        that, so validating with it would reject every drop the drill makes —
        and _drop_database's contract requires SOME validation first."""
        self._drill_backup()
        seen = []
        Mixin = type(self.env['ncollection.saas.subprocess.mixin'])
        with patch.object(type(self.Backup), 'restore_to',
                          return_value='drill_acme'), \
                patch.object(Mixin, '_assert_scratch_db_name',
                             side_effect=lambda db: seen.append(db)), \
                patch.object(Mixin, '_drop_database'):
            self.Backup._cron_restore_drill()
        self.assertEqual(seen, ['drill_acme'],
                         "the scratch-name validator was not the one used")

    # -- the name snapshot (#299) -------------------------------------------

    def test_renaming_a_tenant_keeps_its_backups_restorable(self):
        """The bug: database_name was a stored RELATED field, so it followed a
        rename. The files stayed under the old name while the guard computed
        the root from the new one — every historical backup silently
        unrestorable while looking healthy in the UI."""
        tenant = self._tenant(database_name='oldname')
        backup = self.Backup.create({
            'tenant_id': tenant.id, 'status': 'done',
            'file_path': '/var/lib/odoo/backups/oldname/x.tar.enc'})
        self.assertEqual(backup.database_name, 'oldname')

        # Reachable: the name unlocks outside provisioning/ready.
        tenant.database_status = 'error'
        tenant.database_name = 'newname'

        self.assertEqual(
            backup.database_name, 'oldname',
            "the snapshot followed the rename — that is the bug")
        with patch(BACKUP + '.subprocess.run',
                   return_value=self._mock_run()) as run:
            backup.restore_to('restore_oldname')
            run.assert_called()

    def test_a_supplied_database_name_is_ignored_on_create(self):
        """Deriving is half the guard. Accepting a supplied value would make
        the snapshot caller-chosen, and the write() pin would then merely
        freeze an attacker's string in place — the #288 file_path shape."""
        victim = self._tenant(company_name='Victim', database_name='victimco')
        mine = self._tenant()
        backup = self.Backup.create({
            'tenant_id': mine.id, 'status': 'done',
            'database_name': victim.database_name,      # ignored
            'file_path': '/var/lib/odoo/backups/victimco/x.tar.enc'})

        self.assertEqual(backup.database_name, mine.database_name)
        with patch(BACKUP + '.subprocess.run') as run:
            with self.assertRaises(ValidationError):
                backup.restore_to(mine.database_name)
            run.assert_not_called()

    def test_an_UNSET_snapshot_cannot_be_written_either(self):
        """The CRITICAL the audit reproduced, and the state my first pin
        exempted.

        create() legitimately stamps False for a tenant that is not
        provisioned yet. The pin only raised when the current value was
        truthy — so on exactly those records the snapshot could be written to
        any string, including a live victim's real database name, while the
        docstring promised "immutable once set".

        My earlier test could never reach this branch: `_tenant()` defaults to
        database_name='acme', so its snapshot was always truthy. The state a
        guard exempts is the state its test must start from.
        """
        blank = self._tenant(database_name=False,
                             database_status='not_provisioned')

        # The state is now UNREACHABLE rather than merely guarded: such a
        # backup could never be taken (run_backup dumps database_name) nor
        # restored (the guard has nothing to resolve), and it was the only
        # state where the pin's earlier truthy check could be bypassed.
        with self.assertRaises(ValidationError):
            self.Backup.create({'tenant_id': blank.id, 'status': 'done'})

    def test_the_pin_is_unconditional_not_merely_truthy_guarded(self):
        """Defence in depth behind the create() refusal above. The pin must
        raise for ANY write, not only when the current value is set — that
        exemption was the CRITICAL, and it must not creep back if the create
        refusal is ever relaxed."""
        tenant = self._tenant()
        backup = self.Backup.create({'tenant_id': tenant.id, 'status': 'done'})
        with self.assertRaises(ValidationError):
            backup.write({'database_name': backup.database_name})

    def test_a_copy_does_not_carry_a_stale_file_path(self):
        """copy() re-derives the snapshot from the live tenant but would carry
        file_path verbatim, producing a record whose file can never satisfy
        its own ownership check. Copy nothing rather than copy a lie."""
        tenant = self._tenant()
        backup = self.Backup.create({
            'tenant_id': tenant.id, 'status': 'done',
            'file_path': '/var/lib/odoo/backups/%s/x.tar.enc'
                         % tenant.database_name})
        self.assertFalse(backup.copy().file_path)

    def test_the_snapshot_cannot_be_rewritten_afterwards(self):
        """Pinning is the other half. Without it, derive-then-rewrite reaches
        the same place one write later."""
        mine = self._tenant()
        backup = self.Backup.create({
            'tenant_id': mine.id, 'status': 'done',
            'file_path': '/var/lib/odoo/backups/%s/x.tar.enc'
                         % mine.database_name})
        with self.assertRaises(ValidationError):
            backup.write({'database_name': 'victimco'})

    # -- recycled backup directories (#295) ---------------------------------

    def _backup_root_with(self, *dbs):
        """A temp backup root containing a dump for each named tenant."""
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        for db in dbs:
            d = os.path.join(root, db)
            os.makedirs(d)
            with open(os.path.join(d, 'x.tar.enc'), 'wb') as fh:
                fh.write(b'dump')
        env_patch = patch.dict('os.environ', {'NC_BACKUP_DIR': root})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        return root

    def test_deleting_a_tenant_takes_its_backup_files_with_it(self):
        """tenant_id is ondelete='cascade', so the ROWS went. The FILES stayed,
        under a directory named after a REUSABLE name — so a later tenant given
        that name inherited them, and #288's directory-keyed ownership check
        would have accepted them as its own."""
        root = self._backup_root_with('gonesoon')
        tenant = self._tenant(database_name='gonesoon')
        self.assertTrue(os.path.isdir(os.path.join(root, 'gonesoon')))

        tenant.unlink()
        # The purge is deferred to postcommit so an irreversible rmtree waits
        # for the reversible DELETE to become durable. TransactionCase never
        # commits, so drive the hook explicitly -- this IS the deferral, and a
        # test that passed without it would be testing the old inline path.
        self.env.cr.postcommit.run()

        self.assertFalse(os.path.isdir(os.path.join(root, 'gonesoon')),
                         "the deleted tenant's dumps are still on disk")

    def test_a_traversal_NAME_is_refused_by_the_name_rule(self):
        """First line of defence: the scratch-name regex rejects these before
        any path is built. Kept separate from the containment test below --
        merging them hid the fact that only ONE of the two guards was doing
        the work."""
        root = self._backup_root_with('victimco')
        # Values ONLY the regex rejects. My first version used `..`/`.` —
        # which realpath collapses, so the CONTAINMENT check caught them and
        # deleting the regex left this test green. Uppercase, spaces, empty
        # and too-short all resolve harmlessly INSIDE the root, so nothing
        # but the name rule can refuse them.
        for evil in ('ACME', 'a b', '', 'x', 'has.dot'):
            with self.assertRaises(ValidationError):
                self.Backup._purge_tenant_backup_dir(evil)
        self.assertTrue(os.path.isdir(os.path.join(root, 'victimco')))

    def test_a_SYMLINKED_tenant_dir_pointing_outside_the_root_is_refused(self):
        """The containment assertion, isolated.

        My first attempt at this asserted only on `..`-style names -- which the
        NAME rule already rejects, so deleting the containment check left the
        test green. It proved nothing. A name that PASSES the regex can still
        resolve outside the root if the tenant directory is a SYMLINK, which is
        a real operator move (park one tenant's dumps on a bigger volume).

        rmtree is irreversible; this is the guard that must never be best
        effort.
        """
        root = self._backup_root_with('innocent')
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, True)
        canary = os.path.join(outside, 'someone-elses-data')
        os.makedirs(canary)

        os.symlink(outside, os.path.join(root, 'linked'))   # name is legal

        with self.assertRaises(ValidationError):
            self.Backup._purge_tenant_backup_dir('linked')
        self.assertTrue(os.path.isdir(canary),
                        "the purge followed a symlink out of the backup root")

    def test_another_suites_namespace_is_NEVER_purged(self):
        """New blast radius this ticket introduced, caught by the audit.

        The fixture suites legitimately create REAL ncollection.tenant rows
        named rtclienta/e2eclienta/provclient/albarari — tenant.py says so
        explicitly. Keying the purge on string equality alone meant any record
        squatting on one of those names would take that suite's dumps with it
        when deleted. CLAUDE.md's ownership table is clear that each suite may
        drop only its own, and the suites clean up via their own dropdb, never
        through unlink().
        """
        root = self._backup_root_with('rtclienta', 'e2eclienta', 'provclient')
        for db in ('rtclienta', 'e2eclienta', 'provclient'):
            tenant = self._tenant(database_name=db)
            tenant.unlink()
            self.env.cr.postcommit.run()
            self.assertTrue(
                os.path.isdir(os.path.join(root, db)),
                "deleted another suite's backups for '%s'" % db)

    def test_the_purge_uses_the_STRICT_tenant_name_rule(self):
        """Pins the validator choice, which the sibling #288 code makes
        differently-looking but for the same kind of value. An underscored
        name is a legal SCRATCH name and not a legal TENANT one; on an
        irreversible delete the strict rule is the conservative reading."""
        self._backup_root_with('legit')
        with self.assertRaises(ValidationError):
            self.Backup._purge_tenant_backup_dir('under_scored')

    def test_a_recycled_name_is_refused_while_its_files_remain(self):
        """The live cluster and the blocklist both miss this: dumps outlive the
        tenant AND its database, so a freed name is not actually free."""
        self._backup_root_with('acmetrading')
        Tenant = self.env['ncollection.tenant']
        with patch.object(
                type(self.env['ncollection.provisioning.job']),
                '_database_exists', return_value=False):
            name = Tenant._generate_database_name('Acme Trading')
        self.assertNotEqual(
            name, 'acmetrading',
            "handed out a name whose previous tenant's dumps are still there")
        self.assertRegex(name, r'^[a-z][a-z0-9]{2,62}$')

    def test_a_clean_name_is_still_handed_out(self):
        """The new rejection must not make every name unavailable."""
        self._backup_root_with('somebodyelse')
        Tenant = self.env['ncollection.tenant']
        with patch.object(
                type(self.env['ncollection.provisioning.job']),
                '_database_exists', return_value=False):
            self.assertEqual(
                Tenant._generate_database_name('Acme Trading'), 'acmetrading')

    def test_a_failed_purge_does_not_block_the_delete(self):
        """A filesystem that will not cooperate must not strand a tenant
        record — but it must be LOUD, because a surviving directory is exactly
        what makes a later name reuse dangerous."""
        self._backup_root_with('stubborn')
        tenant = self._tenant(database_name='stubborn')
        with patch('shutil.rmtree', side_effect=OSError('read-only fs')), \
                self.assertLogs(BACKUP, level='ERROR') as caught:
            tenant.unlink()
            self.env.cr.postcommit.run()
        self.assertFalse(tenant.exists(), "the delete was blocked by cleanup")
        self.assertIn('stubborn', '\n'.join(caught.output))

    def test_the_purge_waits_for_the_delete_to_be_durable(self):
        """rmtree has no undo; DELETE does.

        Purging inline meant a rollback later in the SAME transaction brought
        the tenant and its backup ROWS back while the FILES were already gone
        — live records pointing at nothing. Deferring to postcommit makes the
        irreversible half wait for the reversible half to commit.
        """
        root = self._backup_root_with('notyet')
        tenant = self._tenant(database_name='notyet')

        tenant.unlink()
        self.assertTrue(
            os.path.isdir(os.path.join(root, 'notyet')),
            "files were destroyed before the delete was durable")

        self.env.cr.postcommit.run()
        self.assertFalse(os.path.isdir(os.path.join(root, 'notyet')))

    def test_a_malformed_name_on_delete_is_logged_not_raised(self):
        """A tenant in `error` can hold any string (write() unlocks the name
        there). That reaches the scratch-name validator inside the purge and
        raises ValidationError — which must be caught, or a delete would fail
        for a reason unrelated to the delete."""
        self._backup_root_with('legit')
        tenant = self._tenant(database_name='legit')
        tenant.database_status = 'error'
        tenant.database_name = '..'

        with self.assertLogs(TENANT, level='ERROR') as caught:
            tenant.unlink()
            self.env.cr.postcommit.run()
        self.assertFalse(tenant.exists())
        self.assertIn('could not', '\n'.join(caught.output).lower())

    def test_restore_drill_skips_live_tenant_collision(self):
        """ISO-2 (P3-T12): the UNATTENDED monthly drill dropdb+createdb's its
        scratch target, so it must refuse to clobber a live tenant DB — the same
        guard the interactive wizard has."""
        src = self._tenant(database_name='victim')
        backup = self.Backup.create({
            'tenant_id': src.id, 'status': 'done',
            'file_path': '/var/lib/odoo/backups/%s/x.tar.enc' % src.database_name})
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
            'status': 'done',
            'file_path': '/var/lib/odoo/backups/%s/x.dump' % tenant.database_name})

    def test_restoring_over_ANOTHER_live_tenant_is_refused(self):
        """The hole: one RPC drops a live tenant and serves someone else's
        snapshot under its database name."""
        victim = self._tenant(company_name='Victim', database_name='victimco')
        backup = self._done_backup(self._tenant())

        with patch(BACKUP + '.subprocess.run') as run:
            with self.assertRaises(ValidationError):
                backup.restore_to(victim.database_name)
            run.assert_not_called()

    def test_an_ARCHIVED_tenant_is_still_protected(self):
        """The bypass the isolation audit found, and the one I got wrong.

        `sudo()` bypasses ACLs and record rules — it does NOT bypass the
        implicit `active = True` domain Odoo injects on any model with an
        `active` field. Archiving a tenant is a one-click list action that
        touches neither `database_name` nor the physical Postgres database, so
        the row vanished from the clash check while its database stayed live
        and reachable. Restoring another tenant's snapshot over it then
        sailed through — the exact cross-tenant break this ticket closes.
        """
        victim = self._tenant(company_name='Victim', database_name='victimco')
        victim.active = False
        self.assertEqual(
            self.env['ncollection.tenant'].sudo().search_count(
                [('database_name', '=', 'victimco')]), 0,
            "precondition: an archived tenant must be invisible to a plain "
            "search, or this test proves nothing")

        backup = self._done_backup(self._tenant())
        with patch(BACKUP + '.subprocess.run') as run:
            with self.assertRaises(ValidationError):
                backup.restore_to('victimco')
            run.assert_not_called()

    def test_a_backup_cannot_be_repointed_at_another_tenant(self):
        """The guard's "own tenant" exemption is only as good as tenant_id.

        Without this, the whole binding was one write from useless: repoint the
        snapshot at the victim, and restoring over the victim's live database
        becomes "restoring over its own tenant". `database_name` is a stored
        related field, so it follows silently and nothing downstream can tell.
        """
        victim = self._tenant(company_name='Victim', database_name='victimco')
        backup = self._done_backup(self._tenant())

        with self.assertRaises(ValidationError):
            backup.write({'tenant_id': victim.id})

        with patch(BACKUP + '.subprocess.run') as run:
            with self.assertRaises(ValidationError):
                backup.restore_to('victimco')
            run.assert_not_called()

    def test_a_dump_from_ANOTHER_tenant_is_refused(self):
        """#275 bound WHERE a restore lands. This binds WHAT gets restored.

        Without it the same cross-tenant restore was one write away:
        file_path's readonly is UI-only and unenforced over RPC, it is not
        tracked, and the cipher passphrase is platform-wide so any tenant's
        .enc decrypts. Point it at the victim's dump, restore "onto our own
        tenant", and every ownership check above still passes.
        """
        victim = self._tenant(company_name='Victim', database_name='victimco')
        mine = self._tenant()
        backup = self.Backup.create({
            'tenant_id': mine.id, 'status': 'done',
            'file_path': '/var/lib/odoo/backups/%s/stolen.tar.enc'
                         % victim.database_name})

        with patch(BACKUP + '.subprocess.run') as run:
            with self.assertRaises(ValidationError):
                backup.restore_to(mine.database_name)
            run.assert_not_called()

    def test_a_traversal_path_cannot_escape_the_tenant_directory(self):
        """A prefix test is exactly what `..` and symlinks defeat, which is
        why the guard resolves realpath() before comparing."""
        mine = self._tenant()
        for evil in ('/var/lib/odoo/backups/%s/../victimco/x.enc' % mine.database_name,
                     '/var/lib/odoo/backups/%s/../../../etc/passwd' % mine.database_name):
            backup = self.Backup.create({
                'tenant_id': mine.id, 'status': 'done', 'file_path': evil})
            with patch(BACKUP + '.subprocess.run') as run:
                with self.assertRaises(ValidationError):
                    backup.restore_to(mine.database_name)
                run.assert_not_called()

    def test_a_lookalike_sibling_directory_is_refused(self):
        """`/backups/acme2/` must not satisfy a prefix test for tenant `acme`
        — the reason the comparison appends os.sep instead of using
        startswith on the bare root."""
        mine = self._tenant(database_name='acme')
        backup = self.Backup.create({
            'tenant_id': mine.id, 'status': 'done',
            'file_path': '/var/lib/odoo/backups/acme2/x.tar.enc'})
        with patch(BACKUP + '.subprocess.run') as run:
            with self.assertRaises(ValidationError):
                backup.restore_to('acme')
            run.assert_not_called()

    def test_a_REAL_symlink_out_of_the_tenant_dir_is_refused(self):
        """The other traversal tests only collapse `..` lexically — no file
        need exist for that. This plants an actual symlink on disk, because
        "realpath resolves it" was reasoning, not evidence.

        The attack it models: write a symlink INSIDE your own backup
        directory pointing at another tenant's dump. Every string check
        passes — the path really is under your own directory.
        """
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        mine = self._tenant(database_name='minetenant')
        victim_dir = os.path.join(root, 'victimco')
        mine_dir = os.path.join(root, 'minetenant')
        os.makedirs(victim_dir)
        os.makedirs(mine_dir)
        victim_dump = os.path.join(victim_dir, 'secret.tar.enc')
        with open(victim_dump, 'wb') as fh:
            fh.write(b'victim data')
        lure = os.path.join(mine_dir, 'innocent.tar.enc')
        os.symlink(victim_dump, lure)          # lives under MY directory

        backup = self.Backup.create({
            'tenant_id': mine.id, 'status': 'done', 'file_path': lure})

        env_patch = patch.dict('os.environ', {'NC_BACKUP_DIR': root})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        with patch(BACKUP + '.subprocess.run') as run:
            with self.assertRaises(ValidationError):
                backup.restore_to(mine.database_name)
            run.assert_not_called()

    def test_a_path_collapsing_database_name_cannot_widen_the_root(self):
        """The CRITICAL the security review found in my own first version.

        The guard built its root as join(BACKUP_ROOT, tenant.database_name)
        and trusted that name verbatim. `_check_locked_database_name` only
        enforces the format while database_status is 'provisioning' or
        'ready', so a tenant sitting in 'not_provisioned' or 'error' can hold
        ANY string. `.` collapses the root back to the whole backup
        directory — every tenant's dump then satisfies the prefix test — and
        `..` reaches higher still.

        None of the four original RED proofs covered this, because every
        fixture used a well-formed name. The lesson is not "add a case": it is
        that a value must be validated where it is TRUSTED, not where it
        happens to have been checked earlier under different conditions.
        """
        victim = self._tenant(company_name='Victim', database_name='victimco')
        victim_dump = ('/var/lib/odoo/backups/%s/secret.tar.enc'
                       % victim.database_name)

        for evil in ('.', '..', 'x/../y'):
            shadow = self.env['ncollection.tenant'].create({
                'company_name': 'Shadow %s' % evil, 'plan_id': self.plan.id,
                'status': 'active', 'database_status': 'not_provisioned',
                'database_name': evil})
            self.assertEqual(
                shadow.database_name, evil,
                "precondition: the model must actually accept this name, or "
                "the test proves nothing about the guard")
            backup = self.Backup.create({
                'tenant_id': shadow.id, 'status': 'done',
                'file_path': victim_dump})
            with patch(BACKUP + '.subprocess.run') as run:
                with self.assertRaises(ValidationError):
                    backup.restore_to('restorescratch')
                run.assert_not_called()

    def test_the_guard_follows_NC_BACKUP_DIR(self):
        """The script reads NC_BACKUP_DIR; if the guard hardcoded the default,
        a redeployment would break every restore or silently stop checking."""
        mine = self._tenant()
        backup = self.Backup.create({
            'tenant_id': mine.id, 'status': 'done',
            'file_path': '/srv/dumps/%s/x.tar.enc' % mine.database_name})
        with patch.dict('os.environ', {'NC_BACKUP_DIR': '/srv/dumps'}):
            with patch(BACKUP + '.subprocess.run',
                       return_value=self._mock_run()) as run:
                self.assertEqual(backup.restore_to(mine.database_name),
                                 mine.database_name)
                run.assert_called()

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
            'file_path': '/var/lib/odoo/backups/live2/x.tar.enc'})
        wiz = self.env['ncollection.backup.restore.wizard'].create({
            'backup_id': backup.id, 'target_db': 'restore_live2'})
        with patch.object(type(backup), 'with_delay',
                          return_value=MagicMock()) as delayed:
            res = wiz.action_restore()
        delayed.assert_called_once()
        self.assertEqual(res['params']['type'], 'success')
