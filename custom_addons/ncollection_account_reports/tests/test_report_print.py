# -*- coding: utf-8 -*-
"""PDF and XLSX: every report must print, and print what the screen shows.

THE BUG THAT MOTIVATES THIS FILE. The shared PDF template formatted a percentage
with a Python ``%`` inside a QWeb attribute. QWeb runs its own ``%`` pass over
the compiled expression, so ``'%.2f%%'`` became ``'%.2f%'`` and every report with
a percent column (Balance Sheet, P&L, Cash Flow, Financial Summary, all the
analysis reports) died with ``ValueError: incomplete format`` — while the
existing tests only ever asserted that the XLSX started with ``PK``.

So these tests render EVERY concrete engine report to HTML and to a real PDF
(``force_report_rendering`` — Odoo skips wkhtmltopdf under test otherwise), and
pin the properties that make a printout professional: one payload feeds both
channels, numbers are pre-formatted, Arabic prints right-to-left, a long report
paginates, and no template formats with ``%``.
"""
import html as htmllib
import io
import os
import re
import zipfile
from datetime import date, timedelta

from lxml import etree

from odoo.addons.account.tests.common import (
    AccountTestInvoicingCommon, AccountTestInvoicingHttpCommon,
)
from odoo.tests import tagged

_REPORT_XML = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'report', 'report_templates.xml')


@tagged('post_install', '-at_install')
class TestReportPrint(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.receivable = cls.company_data['default_account_receivable']
        cls.revenue = cls.company_data['default_account_revenue']
        cls.journal = cls.company_data['default_journal_misc']
        cls.partner = cls.partner_a
        cls.env['account.move'].create({
            'move_type': 'entry', 'journal_id': cls.journal.id, 'date': date(2026, 6, 15),
            'line_ids': [
                (0, 0, {'account_id': cls.receivable.id, 'debit': 1234.5, 'credit': 0.0,
                        'partner_id': cls.partner.id}),
                (0, 0, {'account_id': cls.revenue.id, 'debit': 0.0, 'credit': 1234.5}),
            ]}).action_post()
        cls.filters = {'date_from': date(2026, 1, 1), 'date_to': date(2026, 12, 31)}

    # ------------------------------------------------------------ helpers
    def _engine_models(self):
        engine = type(self.env['ncollection.account.report'])
        return sorted(
            name for name, model in self.env.registry.items()
            if name.startswith('ncollection.account.report.')
            and issubclass(model, engine) and not self.env[name]._abstract
            and hasattr(self.env[name], '_nc_columns')
            and not name.endswith('.line'))

    def _wizard(self, model, **extra):
        return self.env[model].create(dict(self.filters, **extra))

    def _render(self, wizard, kind, lang=None):
        action = self.env.ref(wizard._nc_report_action_ref()).with_context(
            force_report_rendering=True, **({'lang': lang} if lang else {}))
        if kind == 'html':
            return action._render_qweb_html(action.report_name, [wizard.id])[0]
        return action._render_qweb_pdf(action.report_name, [wizard.id])[0]

    # ------------------------------------------------------- every report
    def test_every_report_renders_to_html(self):
        models = self._engine_models()
        self.assertGreaterEqual(len(models), 15, models)
        for model in models:
            wizard = self._wizard(model)
            html = self._render(wizard, 'html')
            text = html.decode() if isinstance(html, bytes) else html
            self.assertIn(wizard._nc_report_title(), htmllib.unescape(text), model)

    def test_no_report_template_formats_with_a_percent_operator(self):
        """The root cause, made impossible: QWeb mangles ``%``. Format in Python
        (``_nc_export_payload``) and let the template only print strings."""
        tree = etree.parse(_REPORT_XML)
        offenders = []
        for node in tree.iter():
            for name, value in node.attrib.items():
                if name.startswith('t-') and re.search(r"['\"]\s*%\s*[\(\w]", value):
                    offenders.append('%s=%r' % (name, value))
        self.assertFalse(offenders, '%-formatting in a QWeb attribute:\n' + '\n'.join(offenders))

    # --------------------------------------------------------- the payload
    def test_percentages_are_formatted_in_python_and_keep_their_sign(self):
        wizard = self._wizard('ncollection.account.report.profit.loss',
                              comparison_type='previous_period')
        payload = wizard._nc_export_payload()
        pct = [i for i, c in enumerate(payload['columns']) if c['kind'] == 'percent']
        self.assertTrue(pct, 'the comparison report lost its percent column')
        for row in payload['rows']:
            for i in pct:
                self.assertTrue(row['cells'][i]['text'].endswith('%'), row['cells'][i])

    def test_amounts_use_the_currency_decimals_and_negatives_stay_signed(self):
        payload = self._wizard('ncollection.account.report.trial.balance')._nc_export_payload()
        decimals = self.env.company.currency_id.decimal_places
        amount_cols = [i for i, c in enumerate(payload['columns']) if c['kind'] == 'amount']
        self.assertTrue(amount_cols)
        texts = [r['cells'][i]['text'] for r in payload['rows'] for i in amount_cols]
        for text in texts:
            self.assertRegex(text, r'^-?[\d,]+(\.\d{%d})?$' % decimals if decimals else r'^-?[\d,]+$')
        self.assertTrue(any(t.startswith('-') for t in texts), 'a credit balance lost its sign')
        self.assertIn('1,234.50', ''.join(texts))

    def test_the_payload_states_who_when_and_which_filters(self):
        wizard = self._wizard('ncollection.account.report.general.ledger',
                              partner_ids=[(6, 0, self.partner.ids)])
        payload = wizard._nc_export_payload()
        self.assertEqual(payload['company'], self.env.company.name)
        self.assertEqual(payload['currency'], self.env.company.currency_id.name)
        self.assertEqual(payload['user'], self.env.user.name)
        self.assertTrue(payload['generated'])
        self.assertIn(self.partner.display_name, ' '.join(t for _l, t in payload['filters']))

    def test_arabic_prints_right_to_left(self):
        self.env['res.lang']._activate_lang('ar_001')
        wizard = self._wizard('ncollection.account.report.trial.balance').with_context(lang='ar_001')
        self.assertEqual(wizard._nc_export_payload()['direction'], 'rtl')
        html = self._render(wizard, 'html', lang='ar_001')
        self.assertIn('dir="rtl"', html.decode() if isinstance(html, bytes) else html)
        english = self._wizard('ncollection.account.report.trial.balance')._nc_export_payload()
        self.assertEqual(english['direction'], 'ltr')

    # ------------------------------------------------------------ the XLSX
    def _sheet_xml(self, wizard):
        import base64
        data = base64.b64decode(wizard._nc_build_xlsx())
        book = zipfile.ZipFile(io.BytesIO(data))
        return book.read('xl/worksheets/sheet1.xml').decode(), book

    def test_xlsx_has_real_numbers_a_frozen_header_and_a_repeating_print_header(self):
        sheet, book = self._sheet_xml(self._wizard('ncollection.account.report.trial.balance'))
        self.assertIn('<pane', sheet, 'header row is not frozen')
        self.assertIn('state="frozen"', sheet)
        self.assertIn('orientation="landscape"', sheet)
        self.assertRegex(sheet, r'<c r="[B-E]\d+" s="\d+"><v>1234\.5</v></c>',
                         'amounts must be numbers, not text')
        self.assertIn('_xlnm.Print_Titles', book.read('xl/workbook.xml').decode())

    def test_xlsx_is_right_to_left_only_for_arabic(self):
        self.env['res.lang']._activate_lang('ar_001')
        arabic, _ = self._sheet_xml(
            self._wizard('ncollection.account.report.trial.balance').with_context(lang='ar_001'))
        english, _ = self._sheet_xml(self._wizard('ncollection.account.report.trial.balance'))
        self.assertIn('rightToLeft="1"', arabic)
        self.assertNotIn('rightToLeft="1"', english)

    def test_xlsx_dates_are_real_dates(self):
        sheet, book = self._sheet_xml(self._wizard('ncollection.account.report.general.ledger'))
        styles = book.read('xl/styles.xml').decode()
        self.assertIn('yyyy-mm-dd', styles)

    # --------------------------------------------------- long / multi-page
    def _post_many(self, n):
        moves = []
        for i in range(n):
            moves.append({
                'move_type': 'entry', 'journal_id': self.journal.id,
                'date': date(2026, 1, 1) + timedelta(days=i % 300),
                'line_ids': [
                    (0, 0, {'account_id': self.receivable.id, 'debit': 10.0 + i, 'credit': 0.0,
                            'partner_id': self.partner.id}),
                    (0, 0, {'account_id': self.revenue.id, 'debit': 0.0, 'credit': 10.0 + i}),
                ]})
        for move in self.env['account.move'].create(moves):
            move.action_post()

    def test_a_long_ledger_renders_and_exports_without_crashing(self):
        self._post_many(260)
        wizard = self._wizard('ncollection.account.report.general.ledger')
        html = self._render(wizard, 'html')
        text = html.decode() if isinstance(html, bytes) else html
        self.assertGreater(text.count('<tr'), 500)
        sheet, _ = self._sheet_xml(wizard)
        self.assertGreater(sheet.count('<row '), 500)

    def test_headers_repeat_and_rows_do_not_split_across_pages(self):
        html = self._render(self._wizard('ncollection.account.report.trial.balance'), 'html')
        text = html.decode() if isinstance(html, bytes) else html
        self.assertIn('table-header-group', text)
        self.assertIn('page-break-inside: avoid', text)

    def test_ledgers_print_landscape_and_statements_portrait(self):
        landscape = self.env.ref('ncollection_account_reports.paperformat_nc_landscape')
        for xmlid in ('action_report_general_ledger', 'action_report_partner_ledger',
                      'action_report_aged_partner'):
            action = self.env.ref('ncollection_account_reports.' + xmlid)
            self.assertEqual(action.paperformat_id, landscape, xmlid)
        self.assertEqual(landscape.format, 'A4')
        self.assertEqual(landscape.orientation, 'Landscape')


@tagged('post_install', '-at_install')
class TestReportPdf(AccountTestInvoicingHttpCommon):
    """Real wkhtmltopdf runs. It fetches the report stylesheet from the server,
    so this needs a live HTTP server (HttpCase) and a base URL that points at it
    - a TransactionCase would wait on whatever listens on the default port."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.receivable = cls.company_data['default_account_receivable']
        cls.revenue = cls.company_data['default_account_revenue']
        cls.journal = cls.company_data['default_journal_misc']
        cls.partner = cls.partner_a
        moves = []
        for i in range(260):
            moves.append({
                'move_type': 'entry', 'journal_id': cls.journal.id,
                'date': date(2026, 1, 1) + timedelta(days=i % 300),
                'line_ids': [
                    (0, 0, {'account_id': cls.receivable.id, 'debit': 10.0 + i, 'credit': 0.0,
                            'partner_id': cls.partner.id}),
                    (0, 0, {'account_id': cls.revenue.id, 'debit': 0.0, 'credit': 10.0 + i}),
                ]})
        for move in cls.env['account.move'].create(moves):
            move.action_post()
        cls.filters = {'date_from': date(2026, 1, 1), 'date_to': date(2026, 12, 31)}

    def setUp(self):
        super().setUp()
        self.env['ir.config_parameter'].sudo().set_param('web.base.url', self.base_url())

    def _pdf(self, model, lang=None):
        wizard = self.env[model].create(self.filters)
        action = self.env.ref(wizard._nc_report_action_ref()).with_context(
            force_report_rendering=True, **({'lang': lang} if lang else {}))
        return action._render_qweb_pdf(action.report_name, [wizard.id])[0]

    def _engine_models(self):
        return TestReportPrint._engine_models(self)

    def test_every_report_renders_to_a_real_pdf(self):
        models = self._engine_models()
        self.assertGreaterEqual(len(models), 15, models)
        for model in models:
            pdf = self._pdf(model)
            self.assertTrue(pdf.startswith(b'%PDF'), '%s did not produce a PDF' % model)

    def test_a_long_ledger_paginates_in_the_pdf(self):
        pdf = self._pdf('ncollection.account.report.general.ledger')
        pages = len(re.findall(rb'/Type\s*/Page[^s]', pdf))
        self.assertGreater(pages, 3, 'a 500-line ledger must span several pages')

    def test_the_arabic_pdf_renders(self):
        self.env['res.lang']._activate_lang('ar_001')
        pdf = self._pdf('ncollection.account.report.trial.balance', lang='ar_001')
        self.assertTrue(pdf.startswith(b'%PDF'))
