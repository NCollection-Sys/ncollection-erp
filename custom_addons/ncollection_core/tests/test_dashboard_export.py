# -*- coding: utf-8 -*-
"""DASH-02: the dashboard export is the dashboard's own payload, laid out; it adds no calculation and no access."""
import io
import secrets
import zipfile

import openpyxl
import PyPDF2

from odoo import Command
from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.ncollection_core.models.dashboard.dashboard_export import safe_text

PASSWORD = secrets.token_urlsafe(18)


def _payload(figures=None, statement=None):
    return {
        'widgets': (figures or []) + (statement or []),
        'actions': [],
        'meta': {'user_name': 'Probe', 'company_name': 'Probe Co', 'currency': 'AED', 'widget_groups': ['personal'],
                 'period': {'date_from': '2030-01-01', 'date_to': '2030-03-31', 'is_default': False}},
    }


@tagged('post_install', '-at_install')
class TestDashboardExportFile(TransactionCase):

    def setUp(self):
        super().setUp()
        self.service = self.env['ncollection.dashboard.export']

    def _workbook(self, payload):
        return openpyxl.load_workbook(io.BytesIO(self.service.build_xlsx(payload)))

    def test_text_that_looks_like_a_formula_is_neutralised(self):
        for dangerous in ('=1+1', '+SUM(A1)', '-2+3', '@SUM(A1)', '=HYPERLINK("http://x","y")', '\t=1'):
            self.assertTrue(safe_text(dangerous).startswith("'"), dangerous)
        self.assertEqual(safe_text('Acme LLC'), 'Acme LLC')
        self.assertEqual(safe_text(None), '')

    def test_a_formula_in_a_customer_name_is_stored_as_text_never_as_a_formula(self):
        chart = {'key': 'top_customers', 'type': 'chart', 'label': 'Top Customers', 'labels': ['=1+1', '@cmd|x'],
                 'data': [10.0, 20.0], 'format': 'currency'}
        book = self._workbook(_payload([chart]))
        sheet = book['Charts']
        for row in (2, 3):
            cell = sheet.cell(row=row, column=2)
            self.assertEqual(cell.data_type, 's', "a string cell, not a formula (f)")
            self.assertTrue(str(cell.value).startswith("'"))
        with zipfile.ZipFile(io.BytesIO(self.service.build_xlsx(_payload([chart])))) as archive:
            self.assertNotIn(b'<f>', archive.read('xl/worksheets/sheet3.xml'))

    def test_numbers_are_numbers_with_currency_format_and_the_period_is_a_date(self):
        kpi = {'key': 'receivables', 'type': 'kpi', 'label': 'Receivables', 'value': 1234.5, 'format': 'currency',
               'sub': 'Open invoices', 'trend': 12.5}
        count = {'key': 'open_activities', 'type': 'kpi', 'label': 'Open Activities', 'value': 7, 'format': 'integer'}
        book = self._workbook(_payload([kpi, count]))
        figures = book['Key figures']
        self.assertEqual(figures['B2'].value, 1234.5)
        self.assertEqual(figures['B2'].data_type, 'n')
        self.assertIn('#,##0.00', figures['B2'].number_format)
        self.assertEqual(figures['B3'].value, 7)
        self.assertEqual(figures['B3'].number_format, '#,##0')
        self.assertEqual(figures['D2'].value, 12.5)
        summary = book['Summary']
        dates = [c.value for row in summary.iter_rows() for c in row if c.is_date]
        self.assertEqual([d.date().isoformat() for d in dates], ['2030-01-01', '2030-03-31'])

    def test_statement_rows_and_indentation_are_kept(self):
        statement = {'key': 'profit_loss_summary', 'type': 'statement', 'label': 'Profit & Loss',
                     'period': '2030-01-01 -> 2030-03-31',
                     'rows': [{'label': 'Income', 'value': 100.0, 'level': 0, 'key': 'income'},
                              {'label': 'Sales', 'value': -40.0, 'level': 1, 'key': ''}]}
        sheet = self._workbook(_payload(statement=[statement]))['Profit & Loss']
        self.assertEqual((sheet['A5'].value, sheet['B5'].value), ('Income', 100.0))
        self.assertEqual((sheet['A6'].value, sheet['B6'].value), ('Sales', -40.0))
        self.assertEqual(sheet['A6'].alignment.indent, 1)

    def test_the_pdf_is_a_pdf_with_the_figures(self):
        kpi = {'key': 'receivables', 'type': 'kpi', 'label': 'Receivables Probe', 'value': 1234.5,
               'format': 'currency', 'sub': 'Open invoices'}
        pdf = self.service.build_pdf(_payload([kpi]))
        self.assertTrue(pdf.startswith(b'%PDF'))
        reader = PyPDF2.PdfReader(io.BytesIO(pdf))
        text = ' '.join(page.extract_text() for page in reader.pages)
        self.assertIn('Receivables Probe', text)
        self.assertIn('1,234.50', text)

    def test_the_html_is_escaped_and_right_to_left_for_arabic(self):
        kpi = {'key': 'x', 'type': 'kpi', 'label': '<script>alert(1)</script>', 'value': 1, 'format': 'integer'}
        html = self.service.build_html(_payload([kpi]))
        self.assertNotIn('<script>', html)
        self.assertIn('dir="ltr"', html)
        self.env['res.lang']._activate_lang('ar_001')
        arabic = self.service.with_context(lang='ar_001').build_html(_payload([kpi]))
        self.assertIn('dir="rtl"', arabic)

    def test_the_file_name_names_company_and_period_and_is_safe(self):
        payload = _payload()
        payload['meta']['company_name'] = 'A/B "Co" ../x'
        name = self.service.export_filename(payload, 'xlsx')
        self.assertEqual(name, 'NCollection-Summary-A-B-Co-x-2030-01-01_2030-03-31.xlsx')


@tagged('post_install', '-at_install')
class TestDashboardExportRoute(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employee = cls.env['res.users'].create({
            'name': 'Export Employee', 'login': 'export_employee', 'password': PASSWORD,
            'group_ids': [Command.set([cls.env.ref('base.group_user').id,
                                       cls.env.ref('ncollection_core.group_role_employee').id])]})
        cls.other_company = cls.env['res.company'].create({'name': 'Not Mine Co'})

    def _get(self, path):
        return self.url_open(path, allow_redirects=False)

    def test_a_signed_out_visitor_gets_no_file(self):
        response = self._get('/ncollection/dashboard/export/xlsx')
        self.assertNotEqual(response.status_code, 200)
        self.assertNotIn(b'PK', response.content[:2])

    def test_a_signed_in_employee_downloads_both_files_with_good_headers(self):
        self.authenticate('export_employee', PASSWORD)
        xlsx = self._get('/ncollection/dashboard/export/xlsx?date_from=2030-01-01&date_to=2030-03-31')
        self.assertEqual(xlsx.status_code, 200)
        self.assertIn('spreadsheetml', xlsx.headers['Content-Type'])
        self.assertIn('NCollection-Summary-', xlsx.headers['Content-Disposition'])
        self.assertIn('2030-01-01_2030-03-31.xlsx', xlsx.headers['Content-Disposition'])
        book = openpyxl.load_workbook(io.BytesIO(xlsx.content))
        self.assertIn('Key figures', book.sheetnames)
        pdf = self._get('/ncollection/dashboard/export/pdf')
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b'%PDF'))

    def test_the_file_holds_exactly_the_dashboards_figures_for_that_user(self):
        self.authenticate('export_employee', PASSWORD)
        shown = {w['key']: w['value'] for w in self.env['ncollection.dashboard.data'].with_user(
            self.employee).get_dashboard_payload('2030-01-01', '2030-03-31')['widgets'] if w['type'] == 'kpi'}
        book = openpyxl.load_workbook(io.BytesIO(
            self._get('/ncollection/dashboard/export/xlsx?date_from=2030-01-01&date_to=2030-03-31').content))
        rows = list(book['Key figures'].iter_rows(min_row=2, values_only=True))
        self.assertEqual(len(rows), len(shown), "no card the user cannot see, no card missing")
        self.assertEqual(sorted(r[1] for r in rows), sorted(shown.values()))

    def test_a_role_without_financial_access_gets_no_financial_figures(self):
        self.authenticate('export_employee', PASSWORD)
        book = openpyxl.load_workbook(io.BytesIO(self._get('/ncollection/dashboard/export/xlsx').content))
        text = ' '.join(str(c) for row in book['Key figures'].iter_rows(values_only=True) for c in row)
        for forbidden in ('Receivables', 'Payables', 'Cash', 'Net Profit', 'Customer Credit'):
            self.assertNotIn(forbidden, text)
        self.assertNotIn('Profit & Loss', book.sheetnames)

    def test_bad_requests_are_refused(self):
        self.authenticate('export_employee', PASSWORD)
        self.assertEqual(self._get('/ncollection/dashboard/export/csv').status_code, 400)
        self.assertEqual(self._get('/ncollection/dashboard/export/xlsx?date_from=2030-05-01&date_to=2030-01-01')
                         .status_code, 400)
        self.assertEqual(self._get('/ncollection/dashboard/export/xlsx?date_from=2030-01-01').status_code, 400)
        self.assertEqual(self._get('/ncollection/dashboard/export/xlsx?company_id=abc').status_code, 400)

    def test_a_company_the_user_does_not_belong_to_is_forbidden(self):
        self.authenticate('export_employee', PASSWORD)
        response = self._get('/ncollection/dashboard/export/xlsx?company_id=%s' % self.other_company.id)
        self.assertEqual(response.status_code, 403)

    def test_the_route_takes_post_never(self):
        self.authenticate('export_employee', PASSWORD)
        response = self.url_open('/ncollection/dashboard/export/xlsx', data={'x': 1}, allow_redirects=False)
        self.assertNotEqual(response.status_code, 200)
