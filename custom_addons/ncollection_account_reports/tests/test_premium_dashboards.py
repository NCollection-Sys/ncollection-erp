# -*- coding: utf-8 -*-
# flake8: noqa: E501  (user-facing definitions are single translatable sentences)
"""The NCollection Sales and Invoicing dashboards, reconciled to hand-worked records in an AED company.

Fixtures are deterministic (fixed dates and rates). Every figure is checked against what the documents say, and the
empty company proves nothing is ever invented.
"""
import io
import secrets
import zipfile
from datetime import timedelta

import openpyxl

from odoo import Command, fields
from odoo.exceptions import AccessError
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon, AccountTestInvoicingHttpCommon
from odoo.addons.ncollection_core.models.aggregation import cache as aggregation_cache


@tagged('post_install', '-at_install')
class TestPremiumDashboards(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.aed = env.ref('base.AED')
        cls.aed.active = True
        cls.eur = env.ref('base.EUR')
        cls.eur.active = True
        cls.gulf = env['res.company'].create({'name': 'Gulf Trading LLC', 'currency_id': cls.aed.id})
        env['account.chart.template'].with_company(cls.gulf).try_loading('generic_coa', cls.gulf, install_demo=False)
        cls.gulf.currency_id = cls.aed          # the chart template resets the currency; AED is set once it is loaded
        env['res.currency.rate'].create({'name': fields.Date.today() - timedelta(days=4000), 'currency_id': cls.eur.id,
                                         'rate': 0.25, 'company_id': cls.gulf.id})        # 1 EUR = 4 AED
        cls.empty_co = env['res.company'].create({'name': 'Empty AED Co', 'currency_id': cls.aed.id})
        env['account.chart.template'].with_company(cls.empty_co).try_loading('generic_coa', cls.empty_co, install_demo=False)
        cls.empty_co.currency_id = cls.aed
        cls.owner = env['res.users'].create({
            'name': 'Premium Owner', 'login': 'premium_owner', 'company_id': cls.gulf.id,
            'company_ids': [Command.set([cls.gulf.id, cls.empty_co.id, env.company.id])],
            'group_ids': [Command.set([env.ref('base.group_user').id, env.ref('base.group_system').id,
                                       env.ref('ncollection_core.group_role_owner').id])]})
        cls.cx = env['res.partner'].create({'name': 'Customer X'})
        cls.cy = env['res.partner'].create({'name': 'Customer Y'})
        cls.product = env['product.product'].create({'name': 'Premium Item', 'type': 'consu'})

    # ----------------------------------------------------------------------------------------------- helpers
    def setUp(self):
        super().setUp()
        aggregation_cache.clear()

    def _svc(self, company=None):
        company = company or self.gulf
        return self.env['ncollection.premium.dashboard'].with_user(self.owner).with_company(company).with_context(
            allowed_company_ids=[company.id])

    def _kpis(self, payload):
        return {k['key']: k for k in payload['kpis']}

    def _order(self, partner, price, currency, confirm=True, when=None):
        pricelist = self.env['product.pricelist'].sudo().create({'name': 'PL %s' % currency.name,
                                                                 'currency_id': currency.id, 'company_id': False})
        order = self.env['sale.order'].sudo().with_company(self.gulf).create({
            'partner_id': partner.id, 'pricelist_id': pricelist.id, 'company_id': self.gulf.id,
            'order_line': [Command.create({'product_id': self.product.id, 'product_uom_qty': 1, 'price_unit': price,
                                           'tax_ids': [Command.clear()]})]})
        if confirm:
            order.action_confirm()
        if when:
            order.date_order = when
        return order

    def _move(self, amount, move_type='out_invoice', date=None, partner=None, company=None):
        company = company or self.gulf
        journal = self.env['account.journal'].search([('company_id', '=', company.id), ('type', '=', 'sale')], limit=1)
        move = self.env['account.move'].with_company(company).create({
            'move_type': move_type, 'partner_id': (partner or self.cx).id, 'company_id': company.id,
            'invoice_date': date or fields.Date.today(), 'invoice_date_due': date or fields.Date.today(),
            'journal_id': journal.id, 'invoice_payment_term_id': False,
            'invoice_line_ids': [Command.create({'name': 'line', 'quantity': 1, 'price_unit': amount,
                                                 'tax_ids': [Command.clear()]})]})
        move.action_post()
        return move

    def _pay(self, move, amount, date):
        self.env['account.payment.register'].with_company(self.gulf).with_context(
            active_model='account.move', active_ids=move.ids).create(
            {'amount': amount, 'payment_date': date})._create_payments()

    # ----------------------------------------------------------------------------- empty company: nothing invented
    def test_an_empty_aed_company_shows_zeros_and_the_aed_context_never_demo_figures(self):
        for kind in ('sales', 'invoicing'):
            payload = self._svc(self.empty_co).payload(kind)
            self.assertEqual(payload['meta']['currency']['name'], 'AED')
            self.assertEqual(payload['meta']['company'], 'Empty AED Co')
            self.assertTrue(payload['empty'], kind)
            for kpi in payload['kpis']:
                self.assertIn(kpi['value'], (0, 0.0, None), "%s/%s must be zero or undefined, never invented" % (
                    kind, kpi['key']))
                self.assertNotEqual(str(kpi['value']), '-0.0', "%s/%s: a negative zero prints as -0.00" % (
                    kind, kpi['key']))
            for table in payload['tables']:
                self.assertEqual(table['rows'], [], "%s/%s has no rows" % (kind, table['key']))
            for chart in payload['charts']:
                for series in chart['series']:
                    self.assertTrue(all(v == 0 for v in series['data']))

    def test_undefined_figures_are_not_shown_as_zero(self):
        payload = self._svc(self.empty_co).get_sales()
        kpis = self._kpis(payload)
        self.assertIsNone(kpis['conversion']['value'])
        self.assertIsNone(kpis['avg_order']['value'])
        dso = self._kpis(self._svc(self.empty_co).get_invoicing())['dso']
        self.assertIsNone(dso['value'])
        self.assertTrue(dso['note'])

    # ----------------------------------------------------------------------------------- sales with real records
    def test_sales_figures_reconcile_to_the_orders_and_invoices_in_aed(self):
        self._order(self.cx, 100.0, self.aed)
        self._order(self.cy, 50.0, self.eur)                   # 50 EUR = 200 AED
        self._order(self.cx, 40.0, self.aed, confirm=False)    # a quotation
        self._move(500.0)
        self._move(120.0, 'out_refund')
        payload = self._svc().get_sales()
        k = self._kpis(payload)
        self.assertEqual(payload['meta']['currency']['name'], 'AED')
        self.assertEqual(k['orders']['value'], 2)
        self.assertAlmostEqual(k['order_value']['value'], 300.0, 2)
        self.assertAlmostEqual(k['avg_order']['value'], 150.0, 2)
        self.assertEqual(k['quotations']['value'], 1)
        self.assertAlmostEqual(k['conversion']['value'], 2 / 3 * 100.0, 4)
        self.assertAlmostEqual(k['invoiced']['value'], 500.0, 2)
        self.assertAlmostEqual(k['credit_notes']['value'], 120.0, 2)
        self.assertAlmostEqual(k['net_sales']['value'], 380.0, 2)
        tables = {t['key']: t for t in payload['tables']}
        self.assertEqual([(r['label'], r['value']) for r in tables['top_customers']['rows']],
                         [('Customer Y', 200.0), ('Customer X', 100.0)])
        self.assertEqual(len(tables['recent_orders']['rows']), 2)
        self.assertEqual(len(tables['recent_quotes']['rows']), 1)
        self.assertEqual(tables['top_products']['rows'][0]['value'], 300.0)

    def test_the_figures_are_the_active_companys_and_follow_a_company_switch_with_no_stale_cache(self):
        self._order(self.cx, 100.0, self.aed)
        before = self._kpis(self._svc().get_sales())['order_value']['value']
        other = self.env.company
        self.assertAlmostEqual(self._kpis(self._svc(other).get_sales())['order_value']['value'], 0.0, 2,
                               "the other company's payload does not contain this company's order")
        self._order(self.cy, 25.0, self.aed)
        after = self._kpis(self._svc().get_sales())['order_value']['value']
        self.assertAlmostEqual(after - before, 25.0, 2, "a new order is visible at once (nothing is cached)")
        self.assertEqual(self._svc().get_sales()['meta']['company'], 'Gulf Trading LLC')

    def test_other_ticked_companies_are_named_never_added_in(self):
        wide = self.env['ncollection.premium.dashboard'].with_user(self.owner).with_company(self.gulf).with_context(
            allowed_company_ids=[self.gulf.id, self.empty_co.id])
        meta = wide.get_sales()['meta']
        self.assertEqual(meta['currency']['name'], 'AED')
        self.assertIn('Empty AED Co', meta['other_companies'])
        self.assertIn('only', meta['scope_note'])

    def test_the_period_defaults_to_this_month_and_a_bad_range_is_refused(self):
        from odoo.exceptions import UserError
        meta = self._svc().get_sales()['meta']
        self.assertEqual(meta['period']['from'], fields.Date.today().replace(day=1).isoformat())
        with self.assertRaises(UserError):
            self._svc().get_sales('2025-02-01', '2025-01-01')
        with self.assertRaises(UserError):
            self._svc().get_sales('not-a-date', '2025-01-01')

    def test_a_user_without_the_role_is_refused_at_the_rpc(self):
        plain = self.env['res.users'].create({
            'name': 'Plain', 'login': 'premium_plain', 'company_id': self.gulf.id,
            'company_ids': [Command.set([self.gulf.id])],
            'group_ids': [Command.set([self.env.ref('base.group_user').id])]})
        service = self.env['ncollection.premium.dashboard'].with_user(plain).with_company(self.gulf)
        for kind in ('sales', 'invoicing'):
            with self.assertRaises(AccessError):
                service.payload(kind)

    # ---------------------------------------------------------------------------------- invoicing, DSO and credit
    def test_invoicing_figures_reconcile_and_receivables_equal_the_finance_dashboard(self):
        today = fields.Date.today()
        a = self._move(1000.0)
        b = self._move(600.0, partner=self.cy)
        self._pay(b, 200.0, today)                                            # partial
        self._move(100.0, 'out_refund')
        overdue = self._move(300.0, date=today - timedelta(days=40))          # unpaid, due 40 days ago
        payload = self._svc().get_invoicing('%s' % today.replace(day=1), '%s' % today)
        k = self._kpis(payload)
        invoices_in_month = 2 + (1 if (today - timedelta(days=40)).month == today.month else 0)
        self.assertEqual(k['issued']['value'], invoices_in_month)
        self.assertEqual(k['partial']['value'], 1)
        self.assertGreaterEqual(k['unpaid']['value'], 1)
        self.assertAlmostEqual(k['credit_notes']['value'], 100.0, 2)
        owed_now = 1000.0 + 400.0 + 300.0                                      # a + b residual + overdue
        self.assertAlmostEqual(k['outstanding']['value'], owed_now, 2)
        self.assertAlmostEqual(k['overdue']['value'], 300.0, 2)
        self.assertTrue(payload['tables'][0]['rows'], "the overdue table lists the overdue invoice")
        self.assertEqual(payload['tables'][0]['rows'][0]['id'], overdue.id)
        self.assertEqual(payload['tables'][0]['rows'][0]['days'], 40)
        summary = self.env['ncollection.account.report.summary'].with_user(self.owner).with_company(self.gulf).new({})
        self.assertAlmostEqual(k['outstanding']['value'], summary._nc_service_figures()['receivables_owed'], 2)
        self.assertIsNotNone(a)

    def test_dso_is_average_receivables_over_net_credit_sales_times_days(self):
        """March 2025 (31 days). Nothing owed on 28 Feb. Invoice 1,000 on 10 March, 400 paid on 20 March: owed 600 on 31
        March. Average receivables (0 + 600) / 2 = 300; net credit sales 1,000. DSO = 300 / 1,000 x 31 = 9.3 days."""
        invoice = self._move(1000.0, date=fields.Date.to_date('2025-03-10'))
        self._pay(invoice, 400.0, fields.Date.to_date('2025-03-20'))
        payload = self._svc().get_invoicing('2025-03-01', '2025-03-31')
        dso = payload['dso']
        self.assertAlmostEqual(dso['average_receivables'], 300.0, 2)
        self.assertAlmostEqual(dso['net_credit_sales'], 1000.0, 2)
        self.assertEqual(dso['period_days'], 31)
        self.assertAlmostEqual(dso['days'], 9.3, 2)
        self.assertAlmostEqual(self._kpis(payload)['dso']['value'], 9.3, 2)

    def test_dso_nets_credit_notes_out_of_sales_and_leaves_customer_credit_out_of_receivables(self):
        invoice = self._move(1000.0, date=fields.Date.to_date('2025-03-10'))
        self._move(200.0, 'out_refund', date=fields.Date.to_date('2025-03-12'))       # unapplied credit note
        payload = self._svc().get_invoicing('2025-03-01', '2025-03-31')
        dso = payload['dso']
        self.assertAlmostEqual(dso['net_credit_sales'], 800.0, 2, "1,000 invoiced - 200 credit note")
        self.assertAlmostEqual(dso['average_receivables'], 500.0, 2, "owed 1,000 at the end; the 200 credit is not netted")
        self.assertAlmostEqual(dso['days'], 500.0 / 800.0 * 31, 2)
        self.assertIsNotNone(invoice)

    def test_dso_is_undefined_not_zero_when_there_were_no_credit_sales(self):
        payload = self._svc().get_invoicing('2025-06-01', '2025-06-30')
        self.assertIsNone(payload['dso']['days'])
        self.assertIsNone(self._kpis(payload)['dso']['value'])
        self.assertIn('Not defined', payload['dso']['reason'])


@tagged('post_install', '-at_install')
class TestPremiumDashboardExportRoute(AccountTestInvoicingHttpCommon):
    """The files are built from the SERVER payload: formula-safe, authorised, scoped to the session's company."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.password = secrets.token_urlsafe(18)
        env = cls.env
        cls.accountant = env['res.users'].create({
            'name': 'Premium Export Accountant', 'login': 'premium_export_acct', 'password': cls.password,
            'group_ids': [Command.set([env.ref('base.group_user').id, env.ref('account.group_account_manager').id,
                                       env.ref('ncollection_core.group_role_accountant').id])]})
        cls.plain = env['res.users'].create({
            'name': 'Premium Export Plain', 'login': 'premium_export_plain', 'password': cls.password,
            'group_ids': [Command.set([env.ref('base.group_user').id])]})
        evil = env['res.partner'].create({'name': '=HYPERLINK("http://evil.example","pay here")'})
        move = env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': evil.id, 'invoice_date': fields.Date.today(),
            'invoice_payment_term_id': False,
            'invoice_line_ids': [Command.create({'name': 'l', 'quantity': 1, 'price_unit': 250.0,
                                                 'tax_ids': [Command.clear()]})]})
        move.action_post()

    def _get(self, kind, fmt, query=''):
        return self.url_open('/ncollection/premium/%s/export/%s%s' % (kind, fmt, query), allow_redirects=False)

    def test_a_signed_out_visitor_gets_no_file(self):
        self.assertNotEqual(self._get('invoicing', 'xlsx').status_code, 200)

    def test_the_accountant_downloads_a_server_built_formula_safe_workbook_with_the_real_figures(self):
        self.authenticate('premium_export_acct', self.password)
        response = self._get('invoicing', 'xlsx')
        self.assertEqual(response.status_code, 200, response.text[:200])
        self.assertTrue(response.content.startswith(b'PK'))
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        book = openpyxl.load_workbook(io.BytesIO(response.content))
        cells = {c.value for row in book.active.iter_rows() for c in row if c.value is not None}
        self.assertIn(250.0, cells, "the invoiced amount of the real invoice is in the file as a number")
        self.assertIn(self.env.company.currency_id.name, cells)
        strings = [v for v in cells if isinstance(v, str)]
        self.assertFalse([s for s in strings if s.startswith('=')], "no text can be read as a formula")
        self.assertTrue([s for s in strings if s.startswith("'=HYPERLINK")], "the hostile name is kept, as text")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            for name in archive.namelist():
                if name.startswith('xl/worksheets/'):
                    self.assertNotIn(b'<f>', archive.read(name))

    def test_pdf_and_sales_export_work_and_a_user_without_the_role_is_refused(self):
        self.authenticate('premium_export_acct', self.password)
        pdf = self._get('invoicing', 'pdf')
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b'%PDF'))
        self.assertEqual(self._get('sales', 'xlsx').status_code, 200)
        self.authenticate('premium_export_plain', self.password)
        self.assertEqual(self._get('invoicing', 'xlsx').status_code, 403)
        self.assertEqual(self._get('sales', 'pdf').status_code, 403)

    def test_bad_requests_are_refused_and_no_parameter_chooses_a_company(self):
        self.authenticate('premium_export_acct', self.password)
        self.assertEqual(self._get('invoicing', 'csv').status_code, 400)
        self.assertEqual(self._get('banking', 'xlsx').status_code, 400)
        self.assertEqual(self._get('invoicing', 'xlsx', '?date_from=2025-02-01&date_to=2025-01-01').status_code, 400)
        self.assertEqual(self._get('invoicing', 'xlsx', '?date_from=garbage&date_to=2025-01-01').status_code, 400)
        other = self.env['res.company'].create({'name': 'Not Mine Export Co'})
        response = self._get('invoicing', 'xlsx', '?company_id=%s' % other.id)
        self.assertEqual(response.status_code, 200, "the company parameter is ignored: the session's company is used")
        book = openpyxl.load_workbook(io.BytesIO(response.content))
        text = ' '.join(str(c.value) for row in book.active.iter_rows() for c in row if c.value is not None)
        self.assertNotIn('Not Mine Export Co', text)
        self.assertIn(self.env.company.name, text)
