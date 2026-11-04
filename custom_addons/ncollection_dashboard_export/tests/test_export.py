# -*- coding: utf-8 -*-
"""The Export button of the Dashboards page (``/atlas/dashboards``, Odoo's Spreadsheet Dashboards).

The files are built from a validated snapshot of what the page shows: numbers stay numbers, text never becomes a
formula, the PDF and the Excel hold the same cells, a dashboard the user cannot read cannot be exported, and nothing
in the request chooses a tenant, a company or a user.
"""
import io
import json
import secrets
import zipfile

import openpyxl
import PyPDF2

from odoo import Command, http
from odoo.exceptions import ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged

PASSWORD = secrets.token_urlsafe(18)

SNAPSHOT = {
    'filters': ['Period: This month'],
    'sheets': [{'name': 'Sales', 'rows': [
        [['s', 'Customer', 'Customer'], ['s', 'Total', 'Total']],
        [['s', 'Acme', 'Acme'], ['n', 1234.5, '1,234.50 AED']],
        [['s', '=HYPERLINK("http://evil","x")', '=HYPERLINK("http://evil","x")'], ['n', -7.0, '-7.00']],
        [['s', '<script>alert(1)</script>', '<script>alert(1)</script>']],
    ]}],
    'figures': [{'title': 'Revenue', 'lines': [['Revenue', '1,234.50 AED'], ['', '+3% vs last month']]}],
}


@tagged('post_install', '-at_install')
class TestSpreadsheetDashboardExport(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.service = cls.env['ncollection.spreadsheet.dashboard.export']
        cls.snapshot = cls.service.clean_snapshot(dict(SNAPSHOT, name='Sales'))

    def _workbook(self, snapshot=None):
        data = self.service.build_xlsx(snapshot or self.snapshot)
        return data, openpyxl.load_workbook(io.BytesIO(data))

    def test_numbers_stay_numbers_and_text_stays_text(self):
        _data, book = self._workbook()
        sheet = book['Sales']
        self.assertEqual(sheet['B2'].value, 1234.5)
        self.assertEqual(sheet['B3'].value, -7.0)
        self.assertEqual(sheet['A2'].value, 'Acme')

    def test_text_that_looks_like_a_formula_is_never_a_formula(self):
        data, book = self._workbook()
        self.assertEqual(book['Sales']['A3'].value, '\'=HYPERLINK("http://evil","x")')
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for name in archive.namelist():
                if name.startswith('xl/worksheets/'):
                    self.assertNotIn(b'<f>', archive.read(name), "no cell of the file is a formula")

    def test_the_excel_and_the_pdf_hold_the_same_cells(self):
        html = self.service.build_html(self.snapshot)
        for shown in ('1,234.50 AED', 'Acme', '-7.00', '+3% vs last month'):
            self.assertIn(shown, html)
        pdf = self.service.build_pdf(self.snapshot)
        self.assertTrue(pdf.startswith(b'%PDF'))
        text = ' '.join(page.extract_text() for page in PyPDF2.PdfReader(io.BytesIO(pdf)).pages)
        self.assertIn('Acme', text)

    def test_the_html_is_escaped_and_right_to_left_for_arabic(self):
        html = self.service.build_html(self.snapshot)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('dir="ltr"', html)
        self.env['res.lang']._activate_lang('ar_001')
        self.assertIn('dir="rtl"', self.service.with_context(lang='ar_001').build_html(self.snapshot))

    def test_the_snapshot_is_bounded_and_typed(self):
        wide = {'sheets': [{'name': 'x' * 500, 'rows': [[['s', 'y' * 5000, 'y' * 5000]] + [['n', 1, '1']] * 80] * 1200}]}
        cleaned = self.service.clean_snapshot(wide)
        self.assertLessEqual(len(cleaned['sheets'][0]['name']), 80)
        self.assertLessEqual(len(cleaned['sheets'][0]['rows']), 1000)
        self.assertLessEqual(len(cleaned['sheets'][0]['rows'][0]), 60)
        self.assertLessEqual(len(cleaned['sheets'][0]['rows'][0][0][1]), 1000)
        too_many = {'sheets': [{'name': 's', 'rows': [[['n', 1, '1']] * 60] * 1000}] * 5}      # 300 000 cells
        with self.assertRaises(ValidationError):
            self.service.clean_snapshot(too_many)
        for bad in ('not a dict', {'sheets': [{'rows': [[['x', 1, 1]]]}]},
                    {'sheets': [{'rows': [[['n', 'NaN-string', '1']]]}]},
                    {'sheets': [{'rows': [[['n', float('inf'), '1']]]}]},
                    {'sheets': [{'rows': [[['n', True, '1']]]}]}):
            with self.assertRaises(ValidationError):
                self.service.clean_snapshot(bad)

    def test_the_file_name_is_safe(self):
        name = self.service.export_filename('../../etc/passwd "x"\r\nSet-Cookie: a=b', 'xlsx')
        self.assertRegex(name, r'^[A-Za-z0-9_-]+\.xlsx$')


@tagged('post_install', '-at_install')
class TestSpreadsheetDashboardExportRoute(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employee = cls.env['res.users'].create({
            'name': 'Dash Export Employee', 'login': 'dash_export_employee', 'password': PASSWORD,
            'group_ids': [Command.set([cls.env.ref('base.group_user').id])]})
        group = cls.env['spreadsheet.dashboard.group'].create({'name': 'Probe group'})
        cls.dashboard = cls.env['spreadsheet.dashboard'].create({
            'name': 'Probe dashboard', 'dashboard_group_id': group.id,
            'group_ids': [Command.set([cls.env.ref('base.group_user').id])]})
        cls.private = cls.env['spreadsheet.dashboard'].create({
            'name': 'Administrators only', 'dashboard_group_id': group.id,
            'group_ids': [Command.set([cls.env.ref('base.group_system').id])]})

    def _post(self, fmt, dashboard_id=None, payload=None, token=True):
        data = {'dashboard_id': dashboard_id if dashboard_id is not None else self.dashboard.id,
                'payload': payload if payload is not None else json.dumps(SNAPSHOT)}
        if token:
            data['csrf_token'] = http.Request.csrf_token(self)
        return self.url_open('/ncollection/dashboards/export/%s' % fmt, data=data, allow_redirects=False)

    def test_a_signed_out_visitor_gets_no_file(self):
        response = self._post('xlsx', token=False)
        self.assertNotEqual(response.status_code, 200)
        self.assertNotIn(b'PK', response.content[:2])

    def test_a_signed_in_user_downloads_both_files_with_good_headers(self):
        self.authenticate('dash_export_employee', PASSWORD)
        xlsx = self._post('xlsx')
        self.assertEqual(xlsx.status_code, 200, xlsx.text[:200])
        self.assertTrue(xlsx.content.startswith(b'PK'))
        self.assertIn('attachment', xlsx.headers['Content-Disposition'])
        self.assertEqual(xlsx.headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(xlsx.headers['Cache-Control'], 'no-store')
        pdf = self._post('pdf')
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b'%PDF'))

    def test_the_file_is_named_after_the_stored_dashboard_not_the_clients_word(self):
        self.authenticate('dash_export_employee', PASSWORD)
        response = self._post('xlsx', payload=json.dumps(dict(SNAPSHOT, name='ATTACKER CHOSEN')))
        self.assertNotIn('ATTACKER', response.headers['Content-Disposition'])

    def test_bad_requests_are_refused(self):
        self.authenticate('dash_export_employee', PASSWORD)
        self.assertEqual(self._post('csv').status_code, 400)                              # unknown format
        self.assertEqual(self._post('xlsx', dashboard_id='abc').status_code, 400)
        self.assertEqual(self._post('xlsx', payload='{not json').status_code, 400)
        self.assertEqual(self._post('xlsx', payload=json.dumps({'sheets': [{'rows': [[['n', 'x', 'x']]]}]})
                                    ).status_code, 400)
        self.assertEqual(self._post('xlsx', dashboard_id=999999999).status_code, 404)
        self.assertEqual(self._post('xlsx', token=False).status_code, 400)                # no CSRF token

    def test_a_dashboard_the_user_cannot_read_cannot_be_exported(self):
        self.authenticate('dash_export_employee', PASSWORD)
        self.assertEqual(self._post('xlsx', dashboard_id=self.private.id).status_code, 403)
        self.assertEqual(self._post('pdf', dashboard_id=self.private.id).status_code, 403)

    def test_the_route_takes_get_never(self):
        self.authenticate('dash_export_employee', PASSWORD)
        response = self.url_open('/ncollection/dashboards/export/xlsx', allow_redirects=False)
        self.assertIn(response.status_code, (404, 405))
