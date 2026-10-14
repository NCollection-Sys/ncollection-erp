# -*- coding: utf-8 -*-
"""Licensing: cheque printing follows the licence of the Accounting application it sits inside.

The existing NCollection model (ncollection_core Ring 1 / Ring 2, ncollection_subscription plan picker) deliberately
does not gate a native ``ncollection_*`` module BY NAME: a native module is a feature, and what it needs is licensed
through what it extends. This module needs ``account`` (its menus live under the Accounting root, its only record is
``account.payment``), so:

* a tenant whose plan does not license ``account`` loses the menus (Ring 1) and is refused at the ORM (Ring 2);
* a tenant whose plan does licence it keeps both;
* whether the module is INSTALLED in a tenant at all follows Accounting too: it is a COMPANION module, derived from
  ``account`` in ncollection_subscription's one entitlement authority (``models/companions.py``), never named in a
  plan.

No entitlement of its own is added. These tests pin that, so a later change to the menu parent or to the dependency
cannot silently open the feature to an unlicensed tenant."""
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError
from odoo.tests import tagged

MODULE = 'ncollection_cheque_print'


@tagged('post_install', '-at_install')
class TestChequeLicensing(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Config = cls.env['ncollection.workspace.config']
        cls.Menu = cls.env['ir.ui.menu']
        cls.aed = cls.env.ref('base.AED')
        cls.aed.active = True
        cls.template = cls.env.ref('ncollection_cheque_print.template_adcb')
        cls.accountant = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'lic_acct', 'login': 'lic_acct', 'company_id': cls.env.company.id,
            'company_ids': [(6, 0, cls.env.company.ids)],
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('account.group_account_user').id])]})
        cls.cheque_menus = cls.Menu.browse(cls.env['ir.model.data'].search([
            ('module', '=', MODULE), ('model', '=', 'ir.ui.menu')]).mapped('res_id'))

    def _set_plan(self, allowed):
        self.Config.search([]).unlink()
        self.Config.create({'allowed_module_names': allowed})
        self.env.registry.clear_cache()

    def _payment(self):
        return self.env['account.payment'].create({
            'payment_type': 'outbound', 'partner_type': 'supplier', 'amount': 100.0, 'currency_id': self.aed.id,
            'partner_id': self.env['res.partner'].create({'name': 'Synthetic Payee'}).id,
            'journal_id': self.company_data['default_journal_bank'].id})

    def _print(self, user):
        payment = self._payment()
        return self.env['ncollection.cheque.print.job'].with_user(user).create_for_payment(
            payment.with_user(user), self.template)

    # ------------------------------------------------------------------ where the menus live
    def test_every_menu_sits_inside_the_accounting_application(self):
        self.assertTrue(self.cheque_menus)
        root = self.env.ref('account.menu_finance')
        for menu in self.cheque_menus:
            self.assertTrue(menu.parent_path.startswith(root.parent_path), "%s is outside Accounting" % menu.name)

    # ------------------------------------------------------------------ Ring 1 (menus)
    def test_a_tenant_without_accounting_loses_the_menus(self):
        self._set_plan('crm')
        blocked = self.Menu._ncollection_blocked_menu_ids()
        self.assertEqual(set(self.cheque_menus.ids) - blocked, set(),
                         "an unlicensed tenant would still see cheque menus")

    def test_a_tenant_with_accounting_keeps_the_menus(self):
        self._set_plan('account,%s' % MODULE)
        blocked = self.Menu._ncollection_blocked_menu_ids()
        self.assertEqual(set(self.cheque_menus.ids) & blocked, set())

    # ------------------------------------------------------------------ Ring 2 (ORM / RPC)
    def test_a_tenant_without_accounting_is_refused_at_the_orm(self):
        payment = self._payment()
        self._set_plan('crm')
        with self.assertRaises(AccessError) as caught:
            self.env['ncollection.cheque.print.job'].with_user(self.accountant).create_for_payment(
                payment.with_user(self.accountant), self.template)
        self.assertIn('NCollection plan', str(caught.exception))

    def test_a_tenant_with_accounting_can_print(self):
        self._set_plan('account,%s' % MODULE)
        self.assertEqual(self._print(self.accountant).state, 'draft')

    # ------------------------------------------------------------------ the module itself
    def test_no_entitlement_of_its_own(self):
        """Never plan-gated by name (the shared rule for native modules). Its ENTITLEMENT is derived from Accounting by
        ncollection_subscription's companion table (tested there and in ncollection_saas); nothing in this module
        names a plan, a licence or an entitlement."""
        self._set_plan('crm')
        self.assertNotIn(MODULE, self.Menu._ncollection_blocked_module_names())
        module = self.env['ir.module.module'].sudo().search([('name', '=', MODULE)])
        self.assertTrue(module.application)
        self.assertIn('account', module.dependencies_id.mapped('name'))

    def test_the_platform_administrator_is_not_locked_out(self):
        """Ring 2 never applies to the system user; the operator console is unaffected by a tenant plan."""
        self._set_plan('crm')
        admin = self.env.ref('base.user_admin')
        payment = self._payment()
        admin.write({'group_ids': [(4, self.env.ref('account.group_account_user').id)],
                     'company_ids': [(4, payment.company_id.id)]})
        self.assertTrue(admin.has_group('base.group_system'))
        self.assertIsNone(payment.with_user(admin).check_access('read'))
