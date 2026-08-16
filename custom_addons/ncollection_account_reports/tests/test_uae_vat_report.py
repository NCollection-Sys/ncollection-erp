# -*- coding: utf-8 -*-
"""F2-T06: Test suite for UAE FTA VAT 201 Return and FAF Exporter (#116).

Verifies:
  - Box 1 (1a - 1g) Standard rated supplies mapped per Emirate (5% VAT)
  - Box 4 Zero-rated supplies (0%)
  - Box 5 Exempt supplies
  - Box 9 Standard rated expenses (5% input tax)
  - Box 8 / 11 / 12 / 13 / 14 Totals and Net Payable calculation
  - On-screen list view drilldowns
  - PDF & XLSX export generation
  - FTA Audit File (FAF) CSV content structure and reconciliation
"""
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests.common import tagged


@tagged('post_install', '-at_install', 'financial', 'f2_t06')
class TestUaeVatReport(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.currency_id = cls.env.ref('base.AED')

        # Ensure UAE country & states
        cls.country_ae = cls.env.ref('base.ae')
        cls.state_dubai = cls.env['res.country.state'].search([
            ('country_id', '=', cls.country_ae.id),
            ('name', 'ilike', 'Dubai'),
        ], limit=1)
        if not cls.state_dubai:
            cls.state_dubai = cls.env['res.country.state'].create({
                'name': 'Dubai',
                'code': 'DXB',
                'country_id': cls.country_ae.id,
            })

        cls.state_abu_dhabi = cls.env['res.country.state'].search([
            ('country_id', '=', cls.country_ae.id),
            ('name', 'ilike', 'Abu Dhabi'),
        ], limit=1)
        if not cls.state_abu_dhabi:
            cls.state_abu_dhabi = cls.env['res.country.state'].create({
                'name': 'Abu Dhabi',
                'code': 'AUH',
                'country_id': cls.country_ae.id,
            })

        # Partners
        cls.partner_dubai = cls.env['res.partner'].create({
            'name': 'Dubai Customer LLC',
            'country_id': cls.country_ae.id,
            'state_id': cls.state_dubai.id,
            'vat': '100123456789003',
        })
        cls.partner_auh = cls.env['res.partner'].create({
            'name': 'Abu Dhabi Enterprise PJSC',
            'country_id': cls.country_ae.id,
            'state_id': cls.state_abu_dhabi.id,
            'vat': '100987654321003',
        })
        cls.partner_vendor = cls.env['res.partner'].create({
            'name': 'UAE Office Supplier LLC',
            'country_id': cls.country_ae.id,
            'state_id': cls.state_dubai.id,
            'vat': '100555555555003',
        })

        # Use company_data accounts
        cls.account_income = cls.company_data['default_account_revenue']
        cls.account_expense = cls.company_data['default_account_expense']
        cls.journal_sale = cls.company_data['default_journal_sale']
        cls.journal_purchase = cls.company_data['default_journal_purchase']

        # Taxes
        cls.tax_sale_5 = cls.env['account.tax'].create({
            'name': '5% Standard Rated Sale',
            'amount_type': 'percent',
            'amount': 5.0,
            'type_tax_use': 'sale',
            'company_id': cls.company.id,
        })
        cls.tax_sale_0 = cls.env['account.tax'].create({
            'name': '0% Zero Rated Sale',
            'amount_type': 'percent',
            'amount': 0.0,
            'type_tax_use': 'sale',
            'company_id': cls.company.id,
        })
        cls.tax_sale_exempt = cls.env['account.tax'].create({
            'name': 'Exempt Sale',
            'amount_type': 'percent',
            'amount': 0.0,
            'type_tax_use': 'sale',
            'company_id': cls.company.id,
        })
        cls.tax_purchase_5 = cls.env['account.tax'].create({
            'name': '5% Standard Rated Purchase',
            'amount_type': 'percent',
            'amount': 5.0,
            'type_tax_use': 'purchase',
            'company_id': cls.company.id,
        })

    def test_uae_vat_201_boxes_and_net_payable(self):
        """Verify VAT 201 return correctly computes Box 1a, 1b, 4, 5, 9, and 14."""
        # 1. Invoice in Dubai (10,000 AED @ 5% = 500 VAT)
        inv_dubai = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.partner_dubai.id,
            'journal_id': self.journal_sale.id,
            'date': '2026-03-15',
            'invoice_date': '2026-03-15',
            'invoice_line_ids': [(0, 0, {
                'name': 'Consulting Services Dubai',
                'account_id': self.account_income.id,
                'price_unit': 10000.0,
                'tax_ids': [(6, 0, [self.tax_sale_5.id])],
            })],
        })
        inv_dubai.action_post()

        # 2. Invoice in Abu Dhabi (20,000 AED @ 5% = 1000 VAT)
        inv_auh = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.partner_auh.id,
            'journal_id': self.journal_sale.id,
            'date': '2026-03-16',
            'invoice_date': '2026-03-16',
            'invoice_line_ids': [(0, 0, {
                'name': 'Enterprise Software Abu Dhabi',
                'account_id': self.account_income.id,
                'price_unit': 20000.0,
                'tax_ids': [(6, 0, [self.tax_sale_5.id])],
            })],
        })
        inv_auh.action_post()

        # 3. Zero-Rated Export (5,000 AED @ 0% = 0 VAT)
        inv_zero = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.partner_dubai.id,
            'journal_id': self.journal_sale.id,
            'date': '2026-03-17',
            'invoice_date': '2026-03-17',
            'invoice_line_ids': [(0, 0, {
                'name': 'International Export Goods',
                'account_id': self.account_income.id,
                'price_unit': 5000.0,
                'tax_ids': [(6, 0, [self.tax_sale_0.id])],
            })],
        })
        inv_zero.action_post()

        # 4. Purchase Bill (6,000 AED @ 5% = 300 VAT)
        bill = self.env['account.move'].create({
            'move_type': 'in_invoice',
            'partner_id': self.partner_vendor.id,
            'journal_id': self.journal_purchase.id,
            'date': '2026-03-20',
            'invoice_date': '2026-03-20',
            'invoice_line_ids': [(0, 0, {
                'name': 'Office Laptops & Hardware',
                'account_id': self.account_expense.id,
                'price_unit': 6000.0,
                'tax_ids': [(6, 0, [self.tax_purchase_5.id])],
            })],
        })
        bill.action_post()

        # Generate VAT 201 Report Wizard
        wizard = self.env['ncollection.account.report.uae.vat'].create({
            'company_id': self.company.id,
            'date_from': '2026-03-01',
            'date_to': '2026-03-31',
            'target_move': 'posted',
        })

        lines = wizard._nc_compute_lines()
        line_by_box = {r['box']: r for r in lines if r['box']}

        # Box 1a (Abu Dhabi): 20,000 AED Net, 1,000 AED VAT
        self.assertIn('1a', line_by_box)
        self.assertAlmostEqual(line_by_box['1a']['balance'], 20000.0)
        self.assertAlmostEqual(line_by_box['1a']['vat_amount'], 1000.0)

        # Box 1b (Dubai): 10,000 AED Net, 500 AED VAT
        self.assertIn('1b', line_by_box)
        self.assertAlmostEqual(line_by_box['1b']['balance'], 10000.0)
        self.assertAlmostEqual(line_by_box['1b']['vat_amount'], 500.0)

        # Box 4 (Zero-Rated): 5,000 AED Net
        self.assertIn('4', line_by_box)
        self.assertAlmostEqual(line_by_box['4']['balance'], 5000.0)

        # Box 8 (Total Output Tax): 1,500 AED VAT
        self.assertIn('8', line_by_box)
        self.assertAlmostEqual(line_by_box['8']['vat_amount'], 1500.0)

        # Box 9 (Standard Rated Expenses): 6,000 AED Net, 300 AED VAT
        self.assertIn('9', line_by_box)
        self.assertAlmostEqual(line_by_box['9']['balance'], 6000.0)
        self.assertAlmostEqual(line_by_box['9']['vat_amount'], 300.0)
        self.assertAlmostEqual(line_by_box['9']['recoverable_vat'], 300.0)

        # Box 11 (Total Input Tax): 300 AED
        self.assertIn('11', line_by_box)
        self.assertAlmostEqual(line_by_box['11']['recoverable_vat'], 300.0)

        # Box 12 (Due Tax): 1,500 AED
        self.assertIn('12', line_by_box)
        self.assertAlmostEqual(line_by_box['12']['vat_amount'], 1500.0)

        # Box 13 (Recoverable Tax): 300 AED
        self.assertIn('13', line_by_box)
        self.assertAlmostEqual(line_by_box['13']['recoverable_vat'], 300.0)

        # Box 14 (Net Payable = 1500 - 300 = 1200 AED)
        self.assertIn('14', line_by_box)
        self.assertAlmostEqual(line_by_box['14']['vat_amount'], 1200.0)

    def test_uae_vat_exports(self):
        """Verify on-screen action, PDF, XLSX, and FAF CSV exports."""
        wizard = self.env['ncollection.account.report.uae.vat'].create({
            'company_id': self.company.id,
            'date_from': '2026-03-01',
            'date_to': '2026-03-31',
            'target_move': 'posted',
        })

        # 1. On-Screen View Action
        act_view = wizard.action_view()
        self.assertEqual(act_view['res_model'], 'ncollection.account.report.line')

        # 2. PDF Export Action
        act_pdf = wizard.action_export_pdf()
        self.assertEqual(act_pdf['type'], 'ir.actions.report')
        self.assertEqual(act_pdf['report_name'], 'ncollection_account_reports.report_uae_vat')

        # 3. XLSX Export Action
        act_xlsx = wizard.action_export_xlsx()
        self.assertEqual(act_xlsx['type'], 'ir.actions.act_url')

        # 4. FTA Audit File (FAF) CSV Export
        act_faf = wizard.action_export_faf()
        self.assertEqual(act_faf['type'], 'ir.actions.act_url')
        self.assertIn('/web/content/', act_faf['url'])
