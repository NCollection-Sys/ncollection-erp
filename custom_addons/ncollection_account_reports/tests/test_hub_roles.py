# -*- coding: utf-8 -*-
"""Who sees which operational report in the hub - and that the SERVER agrees.

A hidden menu is not security, so every expectation below is checked twice: the
hub catalog (menu + model right) and a direct ORM call as the same user, which is
what an RPC client gets. The personas are the ones the exposure plan names; the
NCollection roles are reused (Sales, Manager, Warehouse, Accountant, CEO, Owner),
and Purchase user / Purchase manager / Inventory manager are plain Odoo groups
assigned to a user - no role was invented for them.
"""
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError
from odoo.tests import tagged

SALES_OPS = {'Quotations', 'Sales Orders', 'Orders to Invoice', 'Sales Order Lines'}
PURCH_OPS = {'Purchase Orders', 'Requests for Quotation', 'Purchase Order Lines'}
INV_OPS = {'Receipts', 'Deliveries', 'Stock by Location', 'Inventory Adjustments'}
REPLENISH = {'Replenishment'}
ALL_OPS = SALES_OPS | PURCH_OPS | INV_OPS | REPLENISH

ROLE = 'ncollection_core.group_role_'


@tagged('post_install', '-at_install')
class TestHubRoles(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from odoo.addons.ncollection_core.hooks import _sync_role_implications
        _sync_role_implications(cls.env)
        cls.Service = cls.env['ncollection.report.service']
        cls.Service._nc_arrange_menus()
        env = cls.env

        def user(login, *groups):
            ids = [env.ref(g).id for g in groups]
            return env['res.users'].create({'name': login, 'login': 'nc_hr_' + login,
                                            'group_ids': [(6, 0, ids)]})
        cls.u = {
            'employee': user('employee', ROLE + 'employee'),
            'sales': user('sales', ROLE + 'sales'),
            'manager': user('manager', ROLE + 'manager'),
            'warehouse': user('warehouse', ROLE + 'warehouse'),
            'accountant': user('accountant', ROLE + 'accountant'),
            'ceo': user('ceo', ROLE + 'ceo'),
            'owner': user('owner', ROLE + 'owner'),
            # plain Odoo groups on top of the Employee floor - no NCollection role
            'purchase_user': user('purchase_user', ROLE + 'employee', 'purchase.group_purchase_user'),
            'purchase_manager': user('purchase_manager', ROLE + 'employee', 'purchase.group_purchase_manager'),
            'inventory_manager': user('inventory_manager', ROLE + 'employee', 'stock.group_stock_manager'),
        }

    def _ops(self, who):
        cat = self.Service.with_user(self.u[who]).nc_report_catalog()
        return {r['name'] for c in cat['categories'] for r in c['reports']} & ALL_OPS

    # ---------------------------------------------------------------- the fixture is real
    def test_the_apps_are_installed_so_this_proves_something(self):
        for module in ('sale', 'purchase', 'stock'):
            self.assertTrue(self.env['ir.module.module'].search(
                [('name', '=', module), ('state', '=', 'installed')]),
                "%s must be installed for these tests to mean anything (CI installs it)" % module)

    # ---------------------------------------------------------------- the hub, per persona
    def test_each_persona_sees_exactly_the_operational_reports_it_is_meant_to(self):
        expected = {
            'employee': set(),
            'sales': SALES_OPS,
            'manager': SALES_OPS | INV_OPS,        # Sales Manager = the Manager role; NO purchasing (D10)
            'warehouse': INV_OPS,                                # Inventory user
            'accountant': set(),                                 # accounting reports only (below)
            'ceo': SALES_OPS | INV_OPS,
            'owner': ALL_OPS,
            'purchase_user': PURCH_OPS,
            'purchase_manager': PURCH_OPS,
            'inventory_manager': INV_OPS | REPLENISH,
        }
        for who, names in expected.items():
            with self.subTest(persona=who):
                self.assertEqual(self._ops(who), names)

    def test_replenishment_stays_administrator_gated(self):
        """Odoo gates it on Inventory ADMINISTRATOR (it carries configuration) - not widened to Inventory User."""
        for who in ('warehouse', 'manager', 'ceo', 'sales', 'purchase_user'):
            self.assertNotIn('Replenishment', self._ops(who), who)

    def test_accounting_users_keep_the_accounting_reports(self):
        cat = self.Service.with_user(self.u['accountant']).nc_report_catalog()
        names = {r['name'] for c in cat['categories'] for r in c['reports']}
        self.assertLessEqual({'Trial Balance', 'General Ledger', 'Tax Analysis', 'Customer Payments'}, names)

    # ---------------------------------------------------------------- the server agrees
    def test_unauthorised_personas_are_refused_by_the_orm_not_just_the_menu(self):
        # Odoo's OWN defaults are kept, not tightened here: stock users read sales orders (delivery status) and
        # purchase users read transfers (receipts), so those pairs are deliberately absent from this list.
        denied = {
            'sale.order': ('employee', 'purchase_user', 'purchase_manager'),
            'purchase.order': ('employee', 'sales'),
            'stock.picking': ('employee',),
        }
        for model, whos in denied.items():
            for who in whos:
                with self.subTest(model=model, persona=who), self.assertRaises(AccessError):
                    self.env[model].with_user(self.u[who]).search_count([])

    def test_the_financial_reports_are_still_refused_to_every_operational_persona(self):
        for who in ('sales', 'manager', 'warehouse', 'purchase_user', 'purchase_manager', 'inventory_manager'):
            with self.subTest(persona=who), self.assertRaises(AccessError):
                self.Service.with_user(self.u[who]).nc_report_run(
                    'ncollection.account.report.trial.balance', {})

    def test_the_oca_ledger_wizards_stay_closed_to_them(self):
        self.assertIn('general.ledger.report.wizard', self.env,
                      'account_financial_report must be installed (CI installs it)')
        for who in ('manager', 'purchase_user', 'inventory_manager'):
            with self.subTest(persona=who), self.assertRaises(AccessError):
                self.env['general.ledger.report.wizard'].with_user(self.u[who]).create({})

    # ---------------------------------------------------------------- no accidental grant
    def test_purchase_and_inventory_personas_hold_no_accounting_configuration_or_admin_group(self):
        forbidden = ('account.group_account_user', 'account.group_account_manager', 'account.group_account_invoice',
                     'account.group_account_readonly', 'base.group_system', 'base.group_erp_manager')
        for who in ('purchase_user', 'purchase_manager', 'inventory_manager'):
            for group in forbidden:
                with self.subTest(persona=who, group=group):
                    self.assertFalse(self.u[who].has_group(group))

    def test_manager_and_ceo_hold_no_purchase_group_because_it_would_give_them_a_financial_write(self):
        """Tried and withdrawn: purchase.group_purchase_user writes vendor bills (account.move), which would give
        Manager a financial write (D5) and the CEO a write where D2 says read-only."""
        for who in ('manager', 'ceo'):
            self.assertFalse(self.u[who].has_group('purchase.group_purchase_user'), who)
            self.assertFalse(self.u[who].has_group('stock.group_stock_manager'), who)
            for right in ('write', 'create', 'unlink'):
                self.assertFalse(self.env['account.move'].with_user(self.u[who]).has_access(right),
                                 '%s must have no ORM %s on journal entries (D5 / D2)' % (who, right))
        self.assertFalse(self.u['manager'].has_group('account.group_account_readonly'))
        self.assertFalse(self.u['ceo'].has_group('account.group_account_user'))
        self.assertFalse(self.u['ceo'].has_group('base.group_system'))

    def test_owner_gains_purchase_and_inventory_administration_it_could_already_reach(self):
        owner = self.u['owner']
        self.assertTrue(owner.has_group('purchase.group_purchase_manager'))
        self.assertTrue(owner.has_group('stock.group_stock_manager'))
        self.assertTrue(owner.has_group('base.group_system'), "Owner was already a system administrator")

    def test_a_manager_sees_no_accounting_menu(self):
        roots = self.env['ir.ui.menu'].with_user(self.u['manager'])._visible_menu_ids(False)
        accounting = self.env.ref('account.menu_finance')          # must exist: an absent menu would pass vacuously
        self.assertNotIn(accounting.id, roots)

    # ---------------------------------------------------------------- record rules and companies
    def test_a_salesperson_sees_only_own_orders_and_a_manager_sees_all(self):
        customer = self.env['res.partner'].create({'name': 'Rule Customer'})
        Order = self.env['sale.order'].sudo()
        mine = Order.create({'partner_id': customer.id, 'user_id': self.u['sales'].id})
        theirs = Order.create({'partner_id': customer.id, 'user_id': self.u['manager'].id})
        seen = Order.with_user(self.u['sales']).search([('id', 'in', (mine | theirs).ids)])
        self.assertEqual(seen, mine, "Sales sees own documents only")
        seen = Order.with_user(self.u['manager']).search([('id', 'in', (mine | theirs).ids)])
        self.assertEqual(seen, mine | theirs, "Manager sees all documents")

    def test_orders_of_another_company_are_invisible_to_a_user_who_cannot_use_it(self):
        other = self.env['res.company'].create({'name': 'Isolated Co'})
        customer = self.env['res.partner'].create({'name': 'Cross Company Customer'})
        so = self.env['sale.order'].sudo().with_company(other).create(
            {'partner_id': customer.id, 'company_id': other.id})
        po = self.env['purchase.order'].sudo().with_company(other).create(
            {'partner_id': customer.id, 'company_id': other.id})
        for who, model, rec in (('manager', 'sale.order', so), ('manager', 'purchase.order', po),
                                ('purchase_user', 'purchase.order', po), ('sales', 'sale.order', so)):
            with self.subTest(persona=who, model=model):
                found = self.env[model].with_user(self.u[who]).search([('id', '=', rec.id)])
                self.assertFalse(found, "a user must not see another company's documents")

    def test_drill_down_is_the_native_record_read_under_the_same_rules(self):
        """A card opens a native list; opening a row is a record read. Refused where the list is."""
        customer = self.env['res.partner'].create({'name': 'Drill Customer'})
        so = self.env['sale.order'].sudo().create({'partner_id': customer.id, 'user_id': self.u['manager'].id})
        with self.assertRaises(AccessError):
            so.with_user(self.u['employee']).read(['name'])
        with self.assertRaises(AccessError):
            so.with_user(self.u['purchase_user']).read(['name'])
        self.assertTrue(so.with_user(self.u['manager']).read(['name']))
