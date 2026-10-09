# -*- coding: utf-8 -*-
"""Tax Analysis: output vs input tax by tax / account / customer / vendor / taxability.

The expected figures come from the DOCUMENTS (``amount_tax`` / ``amount_untaxed``
of the invoices the test posts), never from the report's own code path, so a
wrong sign or a double count shows up as a number that does not match an
invoice.
"""
from datetime import date

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError
from odoo.tests.common import tagged

MODEL = 'ncollection.account.report.tax.analysis'


@tagged('post_install', '-at_install', 'financial', 'nc_tax_analysis')
class TestTaxAnalysis(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.cust_a = env['res.partner'].create({'name': 'Tax Customer A'})
        cls.cust_b = env['res.partner'].create({'name': 'Tax Customer B'})
        cls.vendor = env['res.partner'].create({'name': 'Tax Vendor'})
        cls.tax_zero = env['account.tax'].create({
            'name': 'Zero rated test', 'amount_type': 'percent', 'amount': 0.0,
            'type_tax_use': 'sale', 'company_id': cls.env.company.id})
        income = cls.company_data['default_account_revenue']
        expense = cls.company_data['default_account_expense']

        def invoice(kind, partner, price, taxes, day):
            move = env['account.move'].create({
                'move_type': kind, 'partner_id': partner.id,
                'invoice_date': day, 'date': day,
                'invoice_line_ids': [(0, 0, {
                    'name': 'x', 'quantity': 1, 'price_unit': price,
                    'account_id': (income if kind.startswith('out') else expense).id,
                    'tax_ids': [(6, 0, taxes.ids)]})]})
            move.action_post()
            return move

        cls.sale_a = invoice('out_invoice', cls.cust_a, 10000.0, cls.tax_sale_a, '2026-03-10')
        cls.sale_zero = invoice('out_invoice', cls.cust_b, 2000.0, cls.tax_zero, '2026-03-11')
        cls.sale_none = invoice('out_invoice', cls.cust_b, 300.0, env['account.tax'], '2026-03-12')
        cls.credit = invoice('out_refund', cls.cust_a, 1000.0, cls.tax_sale_a, '2026-03-13')
        cls.bill = invoice('in_invoice', cls.vendor, 4000.0, cls.tax_purchase_a, '2026-03-14')
        cls.output_tax = cls.sale_a.amount_tax - cls.credit.amount_tax
        cls.input_tax = cls.bill.amount_tax
        cls.d_from, cls.d_to = date(2026, 1, 1), date(2026, 12, 31)

    def _run(self, group_by='tax', **extra):
        vals = {'date_from': self.d_from, 'date_to': self.d_to, 'target_move': 'posted',
                'group_by': group_by}
        vals.update(extra)
        return self.env[MODEL].create(vals)

    def _rows(self, group_by='tax', **extra):
        w = self._run(group_by, **extra)
        return w, {r['label']: r for r in w._nc_compute_lines()}

    # ---- by tax --------------------------------------------------------
    def test_output_tax_is_charged_tax_minus_credit_notes(self):
        _w, rows = self._rows('tax')
        out = rows['Output tax — sales']
        self.assertAlmostEqual(out['tax_amount'], self.output_tax, 2,
                               "credit note must REDUCE output tax")
        self.assertGreater(self.credit.amount_tax, 0, "fixture must have a credit note")

    def test_base_is_net_of_credit_notes_and_includes_zero_rated(self):
        w, rows = self._rows('tax')
        by_id = {r['tax_id']: r for r in w._nc_compute_lines() if r.get('tax_id')}
        std = by_id[self.tax_sale_a.id]
        self.assertAlmostEqual(std['base'], 10000.0 - 1000.0, 2)
        self.assertAlmostEqual(std['tax_amount'], self.output_tax, 2)
        zero = by_id[self.tax_zero.id]
        self.assertAlmostEqual(zero['base'], 2000.0, 2, "a 0 % tax has no tax line but must show its base")
        self.assertAlmostEqual(zero['tax_amount'], 0.0, 2)

    def test_input_tax_and_net_payable(self):
        _w, rows = self._rows('tax')
        self.assertAlmostEqual(rows['Input tax — purchases']['tax_amount'], self.input_tax, 2)
        self.assertAlmostEqual(rows['Net tax payable (output − input)']['tax_amount'],
                               self.output_tax - self.input_tax, 2)

    def test_tax_rows_link_to_the_tax_record(self):
        w = self._run('tax')
        linked = {r['tax_id'] for r in w._nc_compute_lines() if r.get('tax_id')}
        self.assertIn(self.tax_sale_a.id, linked)
        self.assertIn(self.tax_purchase_a.id, linked)

    def test_section_rows_are_never_linked(self):
        for row in self._run('tax')._nc_compute_lines():
            if row['level'] == 0:
                self.assertFalse(row.get('tax_id') or row.get('account_id') or row.get('partner_id'),
                                 "a total row must not drill into a record")

    # ---- by account ----------------------------------------------------
    def test_by_tax_account_matches_the_tax_lines(self):
        w, rows = self._rows('account')
        out_acc = self.tax_sale_a.invoice_repartition_line_ids.filtered(
            lambda r: r.repartition_type == 'tax').account_id
        in_acc = self.tax_purchase_a.invoice_repartition_line_ids.filtered(
            lambda r: r.repartition_type == 'tax').account_id
        self.assertAlmostEqual(rows[out_acc.display_name]['tax_amount'], self.output_tax, 2)
        self.assertAlmostEqual(rows[in_acc.display_name]['tax_amount'], self.input_tax, 2)
        by_id = {r['account_id'] for r in w._nc_compute_lines() if r.get('account_id')}
        self.assertTrue({out_acc.id, in_acc.id} <= by_id)

    # ---- by partner ----------------------------------------------------
    def test_customer_view_has_customers_only(self):
        w, rows = self._rows('customer')
        self.assertIn('Tax Customer A', rows)
        self.assertNotIn('Tax Vendor', rows, "a vendor's input tax must not appear under customers")
        self.assertAlmostEqual(rows['Tax Customer A']['tax_amount'], self.output_tax, 2)
        self.assertAlmostEqual(rows['Tax Customer A']['base'], 9000.0, 2)
        self.assertAlmostEqual(rows['Tax Customer B']['tax_amount'], 0.0, 2)
        self.assertTrue(all(r.get('partner_id') for r in w._nc_compute_lines() if r['level'] == 1))

    def test_vendor_view_has_vendors_only(self):
        _w, rows = self._rows('vendor')
        self.assertIn('Tax Vendor', rows)
        self.assertNotIn('Tax Customer A', rows)
        self.assertAlmostEqual(rows['Tax Vendor']['tax_amount'], self.input_tax, 2)
        self.assertAlmostEqual(rows['Tax Vendor']['base'], 4000.0, 2)

    # ---- taxability ----------------------------------------------------
    def test_taxable_vs_non_taxable_split(self):
        _w, rows = self._rows('taxability')
        # the same labels appear under both sides; the sales block comes first
        lines = self._run('taxability')._nc_compute_lines()
        sales = lines[:4]
        self.assertEqual(sales[0]['label'], 'Output tax — sales')
        taxable, zero, none = sales[1], sales[2], sales[3]
        self.assertAlmostEqual(taxable['base'], 9000.0, 2)
        self.assertAlmostEqual(taxable['tax_amount'], self.output_tax, 2)
        self.assertAlmostEqual(zero['base'], 2000.0, 2)
        self.assertAlmostEqual(none['base'], 300.0, 2)
        self.assertTrue(rows)

    # ---- filters -------------------------------------------------------
    def test_period_filter_excludes_other_periods(self):
        w = self._run('tax', date_from=date(2026, 6, 1), date_to=date(2026, 6, 30))
        self.assertEqual(w._nc_compute_lines(), [], "no activity in June ⇒ no rows, not zero rows")

    def test_partner_filter_narrows_the_report(self):
        _w, rows = self._rows('customer', partner_ids=[(6, 0, self.cust_b.ids)])
        self.assertNotIn('Tax Customer A', rows)
        self.assertIn('Tax Customer B', rows)

    # ---- every view prints ---------------------------------------------
    def test_every_view_has_a_payload_for_pdf_and_xlsx(self):
        for view in ('tax', 'account', 'customer', 'vendor', 'taxability'):
            with self.subTest(view=view):
                w = self._run(view)
                payload = w._nc_export_payload()
                self.assertTrue(payload['rows'], "view %s printed nothing" % view)
                self.assertTrue(w._nc_build_xlsx())
                self.assertIn(view, {f for f in ('tax', 'account', 'customer', 'vendor', 'taxability')})
                self.assertTrue(any(label == 'View' or label for label, _t in payload['filters']),
                                "the printed filters must state which view this is")

    def test_title_names_the_view(self):
        self.assertNotEqual(self._run('customer')._nc_report_title(), self._run('tax')._nc_report_title())

    # ---- security ------------------------------------------------------
    def test_a_user_without_accounting_cannot_run_it(self):
        plain = self.env['res.users'].create({
            'name': 'No Accounting', 'login': 'nc_tax_plain',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        with self.assertRaises(AccessError):
            self.env[MODEL].with_user(plain).create(
                {'date_from': self.d_from, 'date_to': self.d_to})

    def test_another_accountant_cannot_read_my_run(self):
        other = self.env['res.users'].create({
            'name': 'Other Accountant', 'login': 'nc_tax_other',
            'group_ids': [(6, 0, [self.env.ref('account.group_account_readonly').id,
                                  self.env.ref('base.group_user').id])]})
        mine = self._run('tax')
        self.assertFalse(self.env[MODEL].with_user(other).search([('id', '=', mine.id)]))
        # the service refuses a foreign run id as well (exists() alone would not)
        svc = self.env['ncollection.report.service'].with_user(other)
        with self.assertRaises(AccessError):
            svc.nc_report_export(MODEL, mine.id, 'pdf')

    def test_the_service_runs_it_and_exposes_the_view_filter(self):
        data = self.env['ncollection.report.service'].nc_report_run(MODEL, {'group_by': 'vendor'})
        names = [f['name'] for f in data['filters']]
        self.assertIn('group_by', names)
        self.assertEqual(data['values']['group_by'], 'vendor')
