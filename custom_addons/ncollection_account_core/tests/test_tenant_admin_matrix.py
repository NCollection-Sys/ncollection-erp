# -*- coding: utf-8 -*-
"""Tenant administration permission MATRIX (final review #7): representative users x Settings sections, checked at
the ORM / route, never as a superuser. Expected outcomes are written down per cell; the matrix is logged so the
release report quotes what ran.

Users (real groups, Option A: the Owner is NOT base.group_system):
  owner        Owner role + Tenant Administrator + Accounting Manager (a provisioned tenant's administrator)
  tenant_admin Tenant Administrator only (an administrator who is not the Owner)
  acc_manager  Accounting Manager
  accountant   Accountant (account.group_account_user)
  readonly     Accounting read-only
  plain        internal user, no business role
"""
import logging

from odoo.exceptions import AccessError
from odoo.tests import HttpCase, new_test_user, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

_logger = logging.getLogger(__name__)
USERS = {
    'owner': 'base.group_user,ncollection_core.group_role_owner,ncollection_core.group_tenant_admin,'
             'account.group_account_manager',
    'tenant_admin': 'base.group_user,ncollection_core.group_tenant_admin',
    'acc_manager': 'base.group_user,account.group_account_manager',
    'accountant': 'base.group_user,account.group_account_user',
    'readonly': 'base.group_user,account.group_account_readonly',
    'plain': 'base.group_user',
}
ADMINS = {'owner', 'tenant_admin'}


@tagged('post_install', '-at_install')
class TestTenantAdminMatrix(HttpCase, AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.users = {name: new_test_user(cls.env, login='nc_mx_%s' % name, password='nc_mx_%s' % name,
                                         groups=groups, company_id=cls.env.company.id)
                     for name, groups in USERS.items()}
        cls.target = new_test_user(cls.env, login='nc_mx_target', groups='base.group_user',
                                   company_id=cls.env.company.id)
        cls.env.company.external_report_layout_id = cls.env.ref('web.external_layout_bubble')
        cls.payment = cls.env['account.payment'].create({
            'payment_type': 'inbound', 'partner_type': 'customer', 'partner_id': cls.partner_a.id, 'amount': 5.0})
        cls.payment.action_post()
        cls.matrix = []

    class _Undo(Exception):
        """Raised after a SUCCESSFUL attempt so its savepoint rolls back: every cell starts from the same state."""

    def _try(self, fn):
        try:
            with self.env.cr.savepoint():
                fn()
                raise self._Undo()
        except self._Undo:
            self.env.invalidate_all()
            return True
        except AccessError:
            self.env.invalidate_all()
            return False

    def _cell(self, user, check, expected, actual):
        self.matrix.append('%-12s %-34s expected %-5s got %-5s' % (user, check, expected, actual))
        self.assertEqual(actual, expected, '%s / %s' % (user, check))

    def test_matrix(self):
        Menu = self.env['ir.ui.menu']
        root = self.env.ref('ncollection_core.menu_workspace_settings_root')
        sections = {
            'menu Users': 'ncollection_core.menu_workspace_users',
            'menu Roles': 'ncollection_core.menu_workspace_roles',
            'menu Company': 'ncollection_core.menu_workspace_company',
            'menu Business Settings': 'ncollection_core.menu_workspace_business_settings',
            'menu Accounting Defaults': 'ncollection_account_core.menu_workspace_accounting_defaults',
            'menu Document Layout': 'ncollection_core.menu_workspace_document_layout',
            'menu Appearance': 'ncollection_core.menu_workspace_appearance',
            'menu Document Stamps': 'ncollection_core.menu_workspace_document_stamps',
            'menu Security': 'ncollection_core.menu_workspace_security',
            'menu Interface Text': 'ncollection_core.menu_workspace_interface_text',
            'menu Delegated Settings': 'ncollection_core.menu_workspace_delegation',
        }
        income = self.company_data['default_account_revenue']
        field = self.env['ir.model.fields']._get('res.partner', 'ref')
        readonly_group = self.env.ref('account.group_account_readonly')
        for name, user in self.users.items():
            admin = name in ADMINS
            env = self.env(user=user)
            self.env.registry.clear_cache()
            visible = Menu.with_user(user)._visible_menu_ids()
            # The Accounting Manager implies Document Stamp Manager (accounting_stamps.xml): Settings shows them the
            # two pages that group already governs (stamps, print settings) and nothing else.
            stamps = name == 'acc_manager'
            self._cell(name, 'Settings app visible', admin or stamps, root.id in visible)
            for label, xmlid in sections.items():
                menu = self.env.ref(xmlid, raise_if_not_found=False)
                if menu:
                    self._cell(name, label, admin or (stamps and label == 'menu Document Stamps'), menu.id in visible)
            self._cell(name, 'save Company phone', admin,
                       self._try(lambda: env.company.write({'phone': '+971 4 555 0000'})))
            self._cell(name, 'save Appearance colour', admin,
                       self._try(lambda: env.company.write({'nc_primary_color': '#224466'})))
            self._cell(name, 'save Document Layout font', admin,
                       self._try(lambda: env.company.write({'font': 'Lato'})))
            # Company-level defaults: administrators, or whoever the section is delegated to (not a role by default).
            self._cell(name, 'save Accounting Default income', admin,
                       self._try(lambda: env.company.write({'income_account_id': income.id})))
            self._cell(name, 'create custom Role', admin,
                       self._try(lambda: env['ncollection.role'].create(
                           {'name': 'MX role %s' % name, 'custom_group_ids': [(6, 0, [readonly_group.id])]})))
            self._cell(name, 'rename a user', admin,
                       self._try(lambda: self.target.with_user(user).nc_update_profile(name='MX %s' % name)))
            self._cell(name, 'change a label', admin, self._try(lambda: env['ncollection.label.override'].create(
                {'target_type': 'field', 'field_id': field.id, 'lang': 'en_US', 'value': 'MX %s' % name})))
            self._cell(name, 'open Delegated Settings', admin,
                       self._try(lambda: env['ncollection.settings.delegation'].action_open()))
            self._cell(name, 'Technical Settings (group_system)', False, user.has_group('base.group_system'))
        _logger.info("TENANT ADMIN MATRIX\n%s", '\n'.join(self.matrix))

    def test_receipt_route_per_role(self):
        allowed = {'owner': 200, 'tenant_admin': 403, 'acc_manager': 200, 'accountant': 200, 'readonly': 200,
                   'plain': 403}
        rows = []
        for name in USERS:
            self.authenticate('nc_mx_%s' % name, 'nc_mx_%s' % name)
            self.env.invalidate_all()
            status = self.url_open('/report/pdf/account.report_payment_receipt/%s' % self.payment.id).status_code
            rows.append('%-12s receipt PDF expected %s got %s' % (name, allowed[name], status))
            self.assertEqual(status, allowed[name], name)
        _logger.info("RECEIPT ROUTE MATRIX\n%s", '\n'.join(rows))
