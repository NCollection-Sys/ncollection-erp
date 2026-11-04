# -*- coding: utf-8 -*-
"""DASH-02: the Finance / Accountant / Cash / Sales / Warehouse dashboards reconciled against hand-worked fixtures.

One group per suspected defect (each written to fail first):
  F  Receivables / Payables are what is OWED; unallocated customer credit and vendor prepayments are shown on their
     own, never netted in. A vendor credit note is not a prepayment. The position is as of the reporting date.
  P  Revenue / expenses / net profit: posted only, credit notes reduce revenue, drafts never count.
  C  Cash is the liquidity ledger; a customer payment in transit is not cash yet.
  K  Average deal size adds orders in ONE currency (the company's).
  W  Warehouse figures add quantities in the product's own unit of measure, and valuation reads Odoo 19's model.
"""
from datetime import timedelta

from odoo import Command, fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestDashboardKpiAcceptance(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for name in ('sale.order', 'purchase.order', 'stock.move', 'stock.quant'):
            assert name in cls.env, "CI installs sale, purchase and stock"
        cls.customer = cls.env['res.partner'].create({'name': 'KPI Customer'})
        cls.vendor = cls.env['res.partner'].create({'name': 'KPI Vendor'})
        cls.eur = cls.env.ref('base.EUR')
        cls.eur.active = True
        cls.env['res.currency.rate'].create({
            'name': fields.Date.today() - timedelta(days=4000), 'currency_id': cls.eur.id, 'rate': 2.0,
            'company_id': cls.env.company.id})
        cls.owner = cls.env['res.users'].create({
            'name': 'KPI Owner', 'login': 'kpi_owner_acc', 'company_id': cls.env.company.id,
            'company_ids': [Command.set(cls.env.company.ids)],
            'group_ids': [Command.set([cls.env.ref('base.group_user').id, cls.env.ref('base.group_system').id,
                                       cls.env.ref('ncollection_core.group_role_owner').id,
                                       cls.env.ref('stock.group_stock_manager').id])]})
        cls.service = cls.env['ncollection.account.dashboard.service'].with_user(cls.owner)
        cls.keeper = cls.env['res.users'].create({
            'name': 'KPI Keeper', 'login': 'kpi_keeper_acc', 'company_id': cls.env.company.id,
            'company_ids': [Command.set(cls.env.company.ids)],
            'group_ids': [Command.set([cls.env.ref('base.group_user').id,
                                       cls.env.ref('ncollection_core.group_role_warehouse').id,
                                       cls.env.ref('stock.group_stock_manager').id])]})
        cls.wh_service = cls.env['ncollection.account.dashboard.service'].with_user(cls.keeper)
        cls.today = fields.Date.today()
        cls._stock_fixture()
        cls.company_data_2 = cls.setup_other_company()

    # ----------------------------------------------------------------------------------------------- helpers
    def _kpis(self, payload):
        return {k['key']: k['value'] for k in payload['kpis']}

    def _finance(self):
        return self._kpis(self.service.get_finance_dashboard())

    def _doc(self, amount, move_type='out_invoice', date=None, post=True):
        partner = self.customer if move_type.startswith('out') else self.vendor
        move = self.env['account.move'].create({
            'move_type': move_type, 'partner_id': partner.id, 'invoice_date': date or self.today,
            'invoice_payment_term_id': False,
            'invoice_line_ids': [Command.create({'name': 'l', 'quantity': 1, 'price_unit': amount,
                                                 'tax_ids': [Command.clear()]})]})
        if post:
            move.action_post()
        return move

    def _payment(self, amount, inbound=True, date=None):
        payment = self.env['account.payment'].create({
            'payment_type': 'inbound' if inbound else 'outbound',
            'partner_type': 'customer' if inbound else 'supplier',
            'partner_id': (self.customer if inbound else self.vendor).id, 'amount': amount,
            'date': date or self.today, 'journal_id': self.company_data['default_journal_bank'].id})
        payment.action_post()
        return payment

    # --------------------------------------------------------------------------------------------------- F
    def test_f_receivables_owed_and_customer_credit_are_shown_separately(self):
        base = self._finance()
        self._doc(100.0)
        self._payment(40.0)                       # an advance: credit, it does not reduce what is owed
        now = self._finance()
        self.assertAlmostEqual(now['receivables'] - base['receivables'], 100.0, 2)
        self.assertAlmostEqual(now['customer_credit'] - base['customer_credit'], 40.0, 2)

    def test_f_payables_owed_and_vendor_prepayments_are_shown_separately(self):
        base = self._finance()
        self._doc(100.0, 'in_invoice')
        self._payment(30.0, inbound=False)        # paid in advance to the supplier
        now = self._finance()
        self.assertAlmostEqual(now['payables'] - base['payables'], 100.0, 2)
        self.assertAlmostEqual(now['vendor_prepayments'] - base['vendor_prepayments'], 30.0, 2)

    def test_f_a_vendor_credit_note_is_not_a_prepayment(self):
        base = self._finance()
        self._doc(20.0, 'in_refund')
        now = self._finance()
        self.assertAlmostEqual(now['vendor_prepayments'] - base['vendor_prepayments'], 0.0, 2,
                               "a supplier credit note is not an advance payment")

    def test_f_the_position_is_as_of_the_reporting_date_not_today(self):
        items = self.env['ncollection.account.open.items'].with_user(self.owner)
        company = self.env.company
        before_owed, _credit = items.position('asset_receivable', self.today - timedelta(days=60), company)
        invoice = self._doc(100.0, date=self.today - timedelta(days=40))
        self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=invoice.ids).create(
            {'amount': 100.0, 'payment_date': self.today - timedelta(days=10)})._create_payments()
        owed_mid, _ = items.position('asset_receivable', self.today - timedelta(days=20), company)
        owed_now, _ = items.position('asset_receivable', self.today, company)
        owed_early, _ = items.position('asset_receivable', self.today - timedelta(days=60), company)
        self.assertAlmostEqual(owed_early - before_owed, 0.0, 2, "not yet invoiced")
        self.assertAlmostEqual(owed_mid - before_owed, 100.0, 2, "invoiced, not yet paid on that date")
        self.assertAlmostEqual(owed_now - before_owed, 0.0, 2, "paid since")

    def test_f_owed_minus_credit_is_the_ledger_balance(self):
        self._doc(100.0)
        self._payment(40.0)
        summary = self.env['ncollection.account.report.summary'].with_user(self.owner).new({})
        ledger = summary._nc_service_figures()['receivables']
        figures = summary._nc_service_figures()
        self.assertAlmostEqual(figures['receivables_owed'] - figures['customer_credit'], ledger, 2)
        self._doc(70.0, 'in_invoice')
        self._payment(25.0, inbound=False)
        self._doc(10.0, 'in_refund')
        figures = summary._nc_service_figures()
        self.assertAlmostEqual(figures['payables_owed'] - figures['vendor_prepayments']
                               - figures['vendor_credit_notes'], figures['payables'], 2)
        self.assertGreater(figures['payables'], 0.0, "a liability is presented as a positive figure")

    # --------------------------------------------------------------------------------------------------- P
    def test_p_revenue_expenses_and_net_profit_follow_posted_documents_only(self):
        before = self._kpis(self.service.get_accountant_dashboard())
        self._doc(100.0)
        self._doc(30.0, 'out_refund')
        self._doc(999.0, post=False)              # a draft is not revenue
        self._doc(20.0, 'in_invoice')
        after = self._kpis(self.service.get_accountant_dashboard())
        self.assertAlmostEqual(after['total_income'] - before['total_income'], 70.0, 2)
        self.assertAlmostEqual(after['net_profit'] - before['net_profit'], 50.0, 2)
        self.assertAlmostEqual((after['operating_expenses'] + after['cogs'])
                               - (before['operating_expenses'] + before['cogs']), 20.0, 2)

    # --------------------------------------------------------------------------------------------------- C
    def test_c_cash_is_the_liquidity_ledger_and_a_payment_in_transit_is_not_cash(self):
        base = self._kpis(self.service.get_cash_dashboard())['cash']
        self._payment(25.0)                       # sits in outstanding receipts until the bank confirms it
        self.assertAlmostEqual(self._kpis(self.service.get_cash_dashboard())['cash'] - base, 0.0, 2)
        bank = self.company_data['default_journal_bank'].default_account_id
        entry = self.env['account.move'].create({
            'move_type': 'entry', 'date': self.today, 'journal_id': self.company_data['default_journal_bank'].id,
            'line_ids': [Command.create({'account_id': bank.id, 'debit': 40.0, 'credit': 0.0}),
                         Command.create({'account_id': self.company_data['default_account_revenue'].id,
                                         'debit': 0.0, 'credit': 40.0})]})
        entry.action_post()
        self.assertAlmostEqual(self._kpis(self.service.get_cash_dashboard())['cash'] - base, 40.0, 2)

    # --------------------------------------------------------------------------------------------------- K
    def _sale(self, price, currency, when):
        pricelist = self.env['product.pricelist'].sudo().create(
            {'name': 'KPI PL %s' % currency.name, 'currency_id': currency.id})
        product = self.env['product.product'].create({'name': 'KPI Item', 'type': 'consu'})
        order = self.env['sale.order'].sudo().create({
            'partner_id': self.customer.id, 'pricelist_id': pricelist.id,
            'order_line': [Command.create({'product_id': product.id, 'product_uom_qty': 1, 'price_unit': price,
                                           'tax_ids': [Command.clear()]})]})
        order.action_confirm()
        order.date_order = when
        return order

    def test_k_average_deal_size_is_in_one_currency(self):
        when = fields.Datetime.to_datetime('2031-06-15 10:00:00')
        self._sale(100.0, self.env.company.currency_id, when)
        self._sale(200.0, self.eur, when)         # 200 EUR = 100 in the company currency
        kpi = self.env['ncollection.kpi'].with_user(self.owner).search([('key', '=', 'avg_deal_size')], limit=1)
        self.assertTrue(kpi)
        average = kpi._compute_avg_deal_size(fields.Date.to_date('2031-06-01'), fields.Date.to_date('2031-07-01'))
        self.assertAlmostEqual(average, 100.0, 2, "(100 + 100) / 2, never (100 + 200) / 2")

    # --------------------------------------------------------------------------------------------------- W
    @classmethod
    def _stock_fixture(cls):
        if hasattr(cls, 'wh_product'):
            return
        env = cls.env
        warehouse = env['stock.warehouse'].search([('company_id', '=', env.company.id)], limit=1)
        cls.stock_loc = warehouse.lot_stock_id
        cls.wh_product = env['product.product'].create({
            'name': 'KPI Stocked', 'type': 'consu', 'is_storable': True, 'standard_price': 5.0})

    def _move(self, src, dst, qty, uom):
        move = self.env['stock.move'].create({
            'product_id': self.wh_product.id, 'product_uom_qty': qty, 'product_uom': uom.id,
            'location_id': src.id, 'location_dest_id': dst.id, 'date': fields.Datetime.now()})
        move._action_confirm()
        move._action_assign()
        move.quantity = qty
        move.picked = True
        move._action_done()
        return move

    def _warehouse_panels(self):
        payload = self.wh_service.get_warehouse_dashboard()
        return {p['key']: p for p in payload['panels']}, payload

    def test_w_quantities_are_added_in_the_products_own_unit(self):
        self._stock_fixture()
        units = self.env.ref('uom.product_uom_unit')
        dozen = self.env.ref('uom.product_uom_dozen')
        supplier = self.env.ref('stock.stock_location_suppliers')
        customer = self.env.ref('stock.stock_location_customers')
        self._move(supplier, self.stock_loc, 24.0, units)
        self._move(self.stock_loc, customer, 1.0, dozen)          # one dozen = 12 units
        panels, _ = self._warehouse_panels()
        row = next(r for r in panels['movement']['rows'] if r['id'] == self.wh_product.id)
        self.assertAlmostEqual(row['value'], 36.0, 2, "24 units + 1 dozen = 36 units, not 25")

    def test_w_inventory_turnover_uses_units_not_move_lines(self):
        self._stock_fixture()
        units = self.env.ref('uom.product_uom_unit')
        dozen = self.env.ref('uom.product_uom_dozen')
        supplier = self.env.ref('stock.stock_location_suppliers')
        customer = self.env.ref('stock.stock_location_customers')
        kpi = self.env['ncollection.kpi'].with_user(self.owner).search([('key', '=', 'inventory_turnover')], limit=1)
        start, end = self.today - timedelta(days=1), self.today + timedelta(days=2)
        on_hand0 = sum(self.env['stock.quant'].sudo().search(
            [('location_id.usage', '=', 'internal')]).mapped('quantity'))
        shipped0 = sum(self.env['stock.move'].sudo().search(
            [('state', '=', 'done'), ('location_dest_id.usage', '=', 'customer'),
             ('date', '>=', start), ('date', '<', end)]).mapped('product_qty'))
        self._move(supplier, self.stock_loc, 24.0, units)
        self._move(self.stock_loc, customer, 1.0, dozen)
        expected = (shipped0 + 12.0) / (on_hand0 + 12.0)
        self.assertAlmostEqual(kpi._compute_inventory_turnover(start, end), expected, 4)

    def test_w_stock_valuation_reads_odoo_19_and_values_the_stock_on_hand(self):
        self._stock_fixture()
        units = self.env.ref('uom.product_uom_unit')
        supplier = self.env.ref('stock.stock_location_suppliers')
        self._move(supplier, self.stock_loc, 12.0, units)
        panels, _ = self._warehouse_panels()
        self.assertIn('valuation', panels, "Odoo 19 has no stock.valuation.layer; the panel must read what exists")
        row = next(r for r in panels['valuation']['rows'] if r['id'] == self.wh_product.id)
        self.assertAlmostEqual(row['value'], 60.0, 2, "12 units at a standard cost of 5")

    # --------------------------------------------------------------------------------------------------- T
    def test_t_the_trend_follows_the_users_calendar_not_the_servers(self):
        from freezegun import freeze_time
        self.owner.tz = 'Pacific/Auckland'            # UTC+13 in March: it is already 1 April there
        with freeze_time('2031-03-31 20:00:00'):
            labels = self.service.get_finance_dashboard()['charts'][0]['labels']
        self.assertEqual(labels[-1], 'Apr 2031', "the user's current month, not the server's")

    # --------------------------------------------------------------------------------------------------- O
    def test_o_one_company_never_adds_in_another_companys_money(self):
        self.owner.company_ids = [Command.link(self.company_data_2['company'].id)]
        other = self.company_data_2['company']
        base_here = self._finance()
        service_b = self.service.with_company(other)
        base_there = self._kpis(service_b.get_finance_dashboard())
        invoice = self.env['account.move'].with_company(other).create({
            'move_type': 'out_invoice', 'partner_id': self.customer.id, 'invoice_date': self.today,
            'company_id': other.id, 'journal_id': self.company_data_2['default_journal_sale'].id,
            'invoice_payment_term_id': False,
            'invoice_line_ids': [Command.create({'name': 'other co', 'quantity': 1, 'price_unit': 300.0,
                                                 'tax_ids': [Command.clear()]})]})
        invoice.action_post()
        self.assertAlmostEqual(self._finance()['receivables'] - base_here['receivables'], 0.0, 2)
        self.assertAlmostEqual(self._kpis(service_b.get_finance_dashboard())['receivables']
                               - base_there['receivables'], 300.0, 2)

    # --------------------------------------------------------------------------------------------------- H
    def test_h_headcount_counts_active_employees_per_department(self):
        if 'hr.employee' not in self.env:
            self.fail("CI installs hr")
        Department = self.env['hr.department'].sudo()
        dept = Department.create({'name': 'KPI Dept'})
        Employee = self.env['hr.employee'].sudo()
        for name in ('A', 'B', 'C'):
            Employee.create({'name': 'KPI %s' % name, 'department_id': dept.id})
        Employee.create({'name': 'KPI Gone', 'department_id': dept.id, 'active': False})
        hr_user = self.env['res.users'].create({
            'name': 'KPI HR', 'login': 'kpi_hr_acc', 'company_id': self.env.company.id,
            'company_ids': [Command.set(self.env.company.ids)],
            'group_ids': [Command.set([self.env.ref('base.group_user').id,
                                       self.env.ref('ncollection_core.group_role_hr').id])]})
        payload = self.env['ncollection.account.dashboard.service'].with_user(hr_user).get_hr_dashboard()
        row = next(r for r in {p['key']: p for p in payload['panels']}['headcount']['rows'] if r['id'] == dept.id)
        self.assertEqual(row['value'], 3, "three active employees; the archived one has left")
