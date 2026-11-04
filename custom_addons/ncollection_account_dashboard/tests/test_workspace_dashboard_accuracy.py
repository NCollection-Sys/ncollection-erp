# -*- coding: utf-8 -*-
"""DASH-01: the Workspace dashboard's figures, reconciled against fixtures whose answers are worked out by hand.

Each group proves ONE suspected defect with a deterministic fixture:
  A  Receivables / Payables must be what is OWED, not the absolute value of a net that mixes invoices with customer
     credit (an unallocated advance is not a reduction of what customers owe).
  B  Sales / Purchases / Top Customers must add amounts in ONE currency (the company's), converted at the order date,
     never a sum of USD and EUR numbers.
  C  "This month" is the USER's month (their time zone), not the server's.
"""
from datetime import datetime, timedelta

from freezegun import freeze_time

from odoo import Command, fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestWorkspaceDashboardAccuracy(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        assert 'sale.order' in cls.env and 'purchase.order' in cls.env, "CI installs sale and purchase"
        cls.customer = cls.env['res.partner'].create({'name': 'Dash Customer'})
        cls.vendor = cls.env['res.partner'].create({'name': 'Dash Vendor'})
        cls.eur = cls.env.ref('base.EUR')
        cls.eur.active = True
        cls.env['res.currency.rate'].create({
            'name': fields.Date.today() - timedelta(days=400), 'currency_id': cls.eur.id, 'rate': 2.0,
            'company_id': cls.env.company.id})          # 1 company-currency unit = 2 EUR, for every date since
        cls.owner = cls.env['res.users'].create({
            'name': 'Dash Owner', 'login': 'dash_owner_acc', 'company_id': cls.env.company.id,
            'company_ids': [Command.set(cls.env.company.ids)],
            'group_ids': [Command.set([cls.env.ref('base.group_user').id, cls.env.ref('base.group_system').id,
                                       cls.env.ref('ncollection_core.group_role_owner').id])]})
        cls.product = cls.env['product.product'].create({'name': 'Dash Item', 'type': 'consu', 'list_price': 10.0})

    def _payload(self, **ctx):
        data = self.env['ncollection.dashboard.data'].with_user(self.owner).with_context(**ctx)
        return {w['key']: w for w in data.get_dashboard_payload()['widgets']}

    def _invoice(self, amount, move_type='out_invoice'):
        partner = self.customer if move_type.startswith('out') else self.vendor
        move = self.env['account.move'].create({
            'move_type': move_type, 'partner_id': partner.id, 'invoice_date': fields.Date.today(),
            'invoice_payment_term_id': False,
            'invoice_line_ids': [Command.create({'name': 'l', 'quantity': 1, 'price_unit': amount,
                                                 'tax_ids': [Command.clear()]})]})
        move.action_post()
        return move

    def _advance(self, amount, inbound=True):
        payment = self.env['account.payment'].create({
            'payment_type': 'inbound' if inbound else 'outbound',
            'partner_type': 'customer' if inbound else 'supplier',
            'partner_id': (self.customer if inbound else self.vendor).id, 'amount': amount,
            'journal_id': self.company_data['default_journal_bank'].id})
        payment.action_post()
        return payment

    # ------------------------------------------------------------------------------------------------ A
    def test_a_receivables_are_what_customers_owe_not_net_of_unallocated_credit(self):
        base = self._payload().get('receivables', {}).get('value', 0.0)
        self._invoice(100.0)
        self._advance(40.0)
        w = self._payload()
        self.assertAlmostEqual(w['receivables']['value'] - base, 100.0, 2,
                               "an unallocated 40 advance is customer credit; it does not reduce what is owed")
        self.assertIn('customer_credit', w, "the unallocated credit is shown on its own")
        self.assertGreaterEqual(w['customer_credit']['value'], 40.0)

    def test_a_payables_are_what_we_owe_not_net_of_vendor_prepayments(self):
        base = self._payload().get('payables', {}).get('value', 0.0)
        self._invoice(100.0, 'in_invoice')
        self._advance(30.0, inbound=False)
        w = self._payload()
        self.assertAlmostEqual(w['payables']['value'] - base, 100.0, 2)

    # ------------------------------------------------------------------------------------------------ B
    def _sale(self, price, currency=None, when=None):
        currency = currency or self.env.company.currency_id
        pricelist = self.env['product.pricelist'].sudo().create({'name': 'PL %s' % currency.name, 'currency_id': currency.id})
        order = self.env['sale.order'].sudo().create({
            'partner_id': self.customer.id, 'pricelist_id': pricelist.id,
            'order_line': [Command.create({'product_id': self.product.id, 'product_uom_qty': 1,
                                           'price_unit': price, 'tax_ids': [Command.clear()]})]})
        order.action_confirm()
        if when:
            order.date_order = when
        return order

    def test_b_sales_this_month_adds_one_currency(self):
        base = self._payload()['sales_this_month']['value']
        self._sale(100.0)                       # 100 in company currency
        self._sale(200.0, self.eur)             # 200 EUR = 100 in company currency (rate 2.0)
        self.assertAlmostEqual(self._payload()['sales_this_month']['value'] - base, 200.0, 2,
                               "100 + (200 EUR = 100) = 200, never 100 + 200")

    def test_b_top_customers_adds_one_currency(self):
        partner = self.env['res.partner'].create({'name': 'ZZ Currency Mix Customer'})
        for price, cur in ((100.0, None), (200.0, self.eur)):
            self._sale(price, cur).partner_id = partner
        board = self._payload()['top_customers']
        by_label = dict(zip(board['labels'], board['data']))
        self.assertAlmostEqual(by_label['ZZ Currency Mix Customer'], 200.0, 2)

    def test_b_purchases_this_month_adds_one_currency(self):
        base = self._payload()['purchases_this_month']['value']
        for price, cur in ((100.0, None), (200.0, self.eur)):
            order = self.env['purchase.order'].sudo().create({
                'partner_id': self.vendor.id, 'currency_id': (cur or self.env.company.currency_id).id,
                'order_line': [Command.create({'product_id': self.product.id, 'product_qty': 1,
                                               'price_unit': price, 'tax_ids': [Command.clear()]})]})
            order.button_confirm()
        self.assertAlmostEqual(self._payload()['purchases_this_month']['value'] - base, 200.0, 2)

    # ------------------------------------------------------------------------------------------------ C
    def test_c_this_month_is_the_users_month(self):
        """Server clock 2031-03-31 20:00 UTC; the user (Auckland, UTC+13 in March) is already on 2031-04-01 09:00.
        An order placed at 2031-03-31 19:00 UTC is 08:00 on 1 April for the user: it is in HIS April."""
        with freeze_time('2031-03-31 20:00:00'):
            self.assertEqual(fields.Date.today().month, 3)
            order_when = datetime(2031, 3, 31, 19, 0, 0)
            base = self._payload(tz='Pacific/Auckland')['sales_this_month']['value']
            self._sale(100.0, when=order_when)
            self.assertAlmostEqual(self._payload(tz='Pacific/Auckland')['sales_this_month']['value'] - base, 100.0, 2,
                                   "the user's month is April: the order belongs to it")

    # --------------------------------------------------------------------- the CEO and Sales dashboards, same rule
    def test_b_ceo_top_customers_and_sales_leaderboard_add_one_currency(self):
        partner = self.env['res.partner'].create({'name': 'ZZ Ceo Mix Customer'})
        for price, cur in ((100.0, None), (200.0, self.eur)):
            order = self._sale(price, cur)
            order.partner_id = partner
            order.user_id = self.owner
        service = self.env['ncollection.account.dashboard.service'].with_user(self.owner)
        today = fields.Date.today()
        rows = service._top_customers(today - timedelta(days=31), today)
        mine = [r for r in rows if r['label'] == 'ZZ Ceo Mix Customer']
        self.assertAlmostEqual(mine[0]['value'], 200.0, 2)
        board = service._ranking_panel({
            'key': 'leaderboard', 'model': 'sale.order', 'domain': [('state', '=', 'sale'), ('user_id', '=', self.owner.id)],
            'groupby': ['user_id'], 'aggregates': ['amount_total:sum'], 'order': 'amount_total:sum desc', 'limit': 5})
        self.assertAlmostEqual(board[0]['value'], 200.0, 2)

