# -*- coding: utf-8 -*-
"""Delete Tenant: who may delete, what is refused before anything is touched, and that a failure is never a success.

The database drop and the filestore removal are mocked: a TransactionCase must not drop a real database. The live
deletion of a disposable tenant is proven against the dev stack. Synthetic users and tenants only.
"""
from unittest.mock import ANY, patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

PLATFORM_GROUP = 'ncollection_subscription.group_platform_admin'
DROP = 'odoo.addons.ncollection_saas.models.saas_subprocess.SaasSubprocessMixin._drop_database'


def _drop(**kwargs):
    """The database drop, mocked. autospec matters: a bare MagicMock answers every attribute, so Odoo's unlink took
    it for an @api.ondelete hook (it looks for ``_ondelete`` on every method) and called it with the tenant."""
    return patch(DROP, autospec=True, **kwargs)


@tagged('post_install', '-at_install')
class TestTenantDeletion(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        internal = cls.env.ref('base.group_user').id
        group = cls.env.ref(PLATFORM_GROUP).id
        cls.alice = cls.env['res.users'].create({'name': 'Alice Del', 'login': 'plat_alice_del',
                                                 'group_ids': [(6, 0, [internal, group])]})
        cls.bob = cls.env['res.users'].create({'name': 'Bob Del', 'login': 'plat_bob_del',
                                               'group_ids': [(6, 0, [internal, group])]})
        cls.clerk = cls.env['res.users'].create({'name': 'Clerk Del', 'login': 'plat_clerk_del',
                                                 'group_ids': [(6, 0, [internal])]})
        cls.Tenant = cls.env['ncollection.tenant'].with_user(cls.env.ref('base.user_root'))
        cls.tenant = cls._tenant('Disposable', 'disposabledel')
        cls.Deletion = cls.env['ncollection.tenant.deletion']

    @classmethod
    def _tenant(cls, name, db, **vals):
        return cls.Tenant.create(dict({
            'company_name': name, 'database_name': db, 'database_status': 'ready', 'status': 'active',
            'platform_admin_ids': [(6, 0, cls.alice.ids)]}, **vals))

    def _delete(self, tenant, typed, user=None):
        with _drop() as drop:
            record = self.Deletion.with_user(user or self.alice)._nc_request(tenant, typed)
        return record, drop

    # ------------------------------------------------------------------ authority
    def test_only_an_authorised_platform_administrator_may_delete(self):
        for user in (self.bob, self.clerk):
            with self.subTest(user=user.login), _drop() as drop, self.assertRaises(AccessError):
                self.Deletion.with_user(user)._nc_request(self.tenant, 'disposabledel')
            drop.assert_not_called()
        with self.assertRaises(AccessError):
            self.env['ncollection.tenant.delete.wizard'].with_user(self.clerk).create({'tenant_id': self.tenant.id})

    # ------------------------------------------------------------------ refusals, before anything is touched
    def test_the_typed_identifier_must_match_exactly(self):
        for typed in ('', 'disposable', 'DISPOSABLEDEL', 'ncollection'):
            with self.subTest(typed=typed), _drop() as drop, self.assertRaises(UserError):
                self.Deletion.with_user(self.alice)._nc_request(self.tenant, typed)
            drop.assert_not_called()
        self.assertTrue(self.tenant.exists())

    def test_the_platform_and_reserved_databases_are_never_deleted(self):
        own = self.env.cr.dbname
        for db in {own, 'ncollection', 'postgres', 'template1', 'saastest'}:
            # A row naming a protected database (the name lock is bypassed as the superuser, which is the point).
            self.env.cr.execute("UPDATE ncollection_tenant SET database_name = %s WHERE id = %s", (db, self.tenant.id))
            self.tenant.invalidate_recordset()
            with self.subTest(db=db), _drop() as drop, self.assertRaises(AccessError):
                self.Deletion.with_user(self.alice)._nc_request(self.tenant, db)
            drop.assert_not_called()

    def test_busy_or_protected_tenants_are_refused(self):
        self.env['ir.config_parameter'].sudo().set_param('ncollection_saas.tenant_delete_protected', 'keepme, other')
        cases = (
            self._tenant('Kept', 'keepme'),
            self._tenant('Provisioning', 'provisioningdel', database_status='provisioning'),
            self._tenant('Upgrading', 'upgradingdel', nc_release_state='upgrading'),
        )
        for tenant in cases:
            with self.subTest(db=tenant.database_name), _drop() as drop, self.assertRaises(UserError):
                self.Deletion.with_user(self.alice)._nc_request(tenant, tenant.database_name)
            drop.assert_not_called()

    # ------------------------------------------------------------------ the run
    def test_a_deletion_drops_the_trusted_database_and_removes_the_platform_records(self):
        tenant_id = self.tenant.id
        with patch('odoo.addons.ncollection_saas.models.tenant_deletion.shutil.rmtree'):
            record, drop = self._delete(self.tenant, 'disposabledel')
        drop.assert_called_once_with(ANY, 'disposabledel')
        self.assertEqual(record.state, 'deleted')
        self.assertTrue(record.database_dropped and record.filestore_removed and record.records_removed)
        self.assertEqual(record.requested_by, self.alice)
        self.assertEqual((record.database_name, record.company_name), ('disposabledel', 'Disposable'))
        self.assertFalse(self.env['ncollection.tenant'].browse(tenant_id).exists())
        self.assertTrue(record.exists(), "the audit record outlives the tenant")

    def test_a_failure_is_recorded_as_a_failure_and_retry_resumes(self):
        with _drop(side_effect=RuntimeError('database busy')):
            record = self.Deletion.with_user(self.alice)._nc_request(self.tenant, 'disposabledel')
        self.assertEqual(record.state, 'failed')
        self.assertFalse(record.database_dropped)
        self.assertIn('database', record.message)
        self.assertTrue(self.tenant.exists(), "nothing past the failed step ran")
        with _drop() as drop:
            record.with_user(self.alice).action_retry()
        drop.assert_called_once_with(ANY, 'disposabledel')
        self.assertEqual(record.state, 'deleted')
        with self.assertRaises(UserError):
            record.with_user(self.alice).action_retry()

    def test_a_plain_unlink_cannot_orphan_a_live_database(self):
        # Odoo's own Delete / an RPC unlink of a tenant whose database exists is refused for everyone, the superuser
        # included: only Delete Tenant (database, files, then the record) removes it.
        self.env.cr.execute("UPDATE ncollection_tenant SET database_name = %s WHERE id = %s",
                            (self.env.cr.dbname, self.tenant.id))
        self.tenant.invalidate_recordset()
        for user in (self.alice, self.env.ref('base.user_root')):
            with self.subTest(user=user.login), self.assertRaises(UserError):
                self.tenant.with_user(user).unlink()
        self.assertTrue(self.tenant.exists())
        # a record whose database never existed is removed as before
        ghost = self._tenant('Ghost', 'ghostnodbdel')
        ghost.unlink()
        self.assertFalse(ghost.exists())

    def test_the_filestore_must_resolve_inside_the_storage_root(self):
        with self.assertRaises(UserError):
            self.Deletion._nc_filestore_path('..')
        self.assertTrue(self.Deletion._nc_filestore_path('disposabledel').endswith('/filestore/disposabledel'))
