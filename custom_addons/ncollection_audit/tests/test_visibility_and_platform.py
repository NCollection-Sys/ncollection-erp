"""Audit Log visibility for the people entitled to it, and the platform's read-only window (#9 of the final review).

Synthetic users, real groups; the service is called AS the config-sync service account, the way JSON-2 does."""
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged

from .common import AuditCommon

ACTOR = {'ref': 'u7', 'name': 'Alice Platform'}


@tagged('post_install', '-at_install')
class TestAuditVisibility(AuditCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tenant_admin = new_test_user(cls.env, login='nc_aud_admin',
                                         groups='base.group_user,ncollection_core.group_tenant_admin')
        cls.plain = new_test_user(cls.env, login='nc_aud_plain', groups='base.group_user')
        cls.service_user = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Sync', 'login': 'sync.audit@ncollection.internal',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('ncollection_core.group_config_sync').id])]})
        cls.root = cls.env.ref('ncollection_audit.menu_nc_audit_root')
        country = cls.env['res.country'].search([], limit=1)
        country.write({'name': country.name + ' '})  # an audited write (probe rule on res.country)

    def _visible(self, user):
        return self.root.id in self.env['ir.ui.menu'].with_user(user)._visible_menu_ids()

    def test_workspace_administrator_sees_and_reads_but_cannot_change(self):
        self.assertFalse(self.tenant_admin.has_group('base.group_system'), "not a technical administrator")
        self.assertTrue(self._visible(self.tenant_admin))
        logs = self.Log.with_user(self.tenant_admin).search([])
        self.assertTrue(logs, "existing history is readable")
        with self.assertRaises(AccessError):
            logs[:1].with_user(self.tenant_admin).unlink()
        with self.assertRaises(AccessError):
            logs[:1].with_user(self.tenant_admin).write({'name': 'x'})

    def test_ordinary_user_neither_sees_nor_reads(self):
        self.assertFalse(self._visible(self.plain))
        with self.assertRaises(AccessError):
            self.Log.with_user(self.plain).search([])

    def test_menu_is_a_home_app_not_technical_settings(self):
        self.assertFalse(self.root.parent_id, "a root menu: its own Home tile")
        self.assertTrue(self.root.action, "the launcher shows only roots that open something")
        self.assertTrue(self.root.web_icon)
        self.assertEqual(self.env.ref('ncollection_audit.menu_nc_audit_seal').parent_id, self.root)
        apps = {app.get('xmlid') or app.get('id') for app in
                self.env['ir.ui.menu'].with_user(self.tenant_admin).nc_tenant_apps()}
        self.assertTrue({'ncollection_audit.menu_nc_audit_root', self.root.id} & apps, apps)
        plain_apps = {app.get('xmlid') or app.get('id') for app in
                      self.env['ir.ui.menu'].with_user(self.plain).nc_tenant_apps()}
        self.assertFalse({'ncollection_audit.menu_nc_audit_root', self.root.id} & plain_apps)

    def test_platform_window_is_metadata_only_and_bounded(self):
        service = self.env['ncollection.admin.service'].with_user(self.service_user)
        reply = service.list_audit_logs({'actor': dict(ACTOR), 'limit': 5, 'model': 'res.country'})
        self.assertTrue(reply['available'])
        self.assertGreaterEqual(reply['total'], 1)
        row = reply['rows'][0]
        self.assertEqual(set(row), {'id', 'date', 'user', 'model', 'model_name', 'res_id', 'record', 'method',
                                    'changes'}, "never old / new values")
        for bad in ({'limit': 0}, {'limit': 101}, {'offset': -1}, {'model': "x'; drop"}, {'date_from': 'yesterday'},
                    {'values': True}):
            with self.subTest(bad=bad), self.assertRaises(UserError):
                service.list_audit_logs({'actor': dict(ACTOR), **bad})

    def test_only_the_service_account_reaches_the_platform_window(self):
        for user in (self.tenant_admin, self.plain):
            with self.subTest(user=user.login), self.assertRaises(AccessError):
                self.env['ncollection.admin.service'].with_user(user).list_audit_logs({'actor': dict(ACTOR)})
