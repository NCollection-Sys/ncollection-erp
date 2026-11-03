# -*- coding: utf-8 -*-
"""Option A: Workspace Administration > Security. The workspace Owner (group_tenant_admin, not base.group_system)
reads this workspace's own audit log - every row of a tenant database is that tenant's - but can never change or
delete it (tamper evidence), and an ordinary user cannot read it. Password-reset tokens stay platform-only."""
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestWorkspaceAuditAccess(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = new_test_user(cls.env, login='aa_owner',
                                  groups='base.group_user,ncollection_core.group_role_owner')
        cls.clerk = new_test_user(cls.env, login='aa_clerk',
                                  groups='base.group_user')
        cls.Log = cls.env['ncollection.auth.log']
        cls.row = cls.Log.sudo().create(cls.Log._prepare_capture_vals('admin_settings_change', user=cls.owner))

    def test_the_owner_reads_the_workspace_audit_log(self):
        self.assertIn(self.row, self.Log.with_user(self.owner).search([]))

    def test_the_owner_cannot_change_or_delete_it(self):
        with self.assertRaises(AccessError):
            self.row.with_user(self.owner).write({'detail': 'rewritten'})
        with self.assertRaises(AccessError):
            self.row.with_user(self.owner).unlink()

    def test_an_ordinary_user_cannot_read_it(self):
        with self.assertRaises(AccessError):
            self.Log.with_user(self.clerk).search([])

    def test_reset_tokens_stay_platform_only(self):
        with self.assertRaises(AccessError):
            self.env['ncollection.auth.spent_token'].with_user(self.owner).search([])
