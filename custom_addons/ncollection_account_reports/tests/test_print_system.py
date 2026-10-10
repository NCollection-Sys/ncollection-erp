# -*- coding: utf-8 -*-
"""The NCollection print design system: what the shared print layer must guarantee.

``test_report_print`` proves every report renders and prints the same numbers as the XLSX. This file pins
what makes the printout a *designed document*: the shared layout is used by every report, ledgers print
one titled section per account / partner with a total, a statement is one document per partner with a
summary, the print layer reuses (never recomputes) the engine's rows, and the CSS stays inside what
wkhtmltopdf 0.12.6 can draw (hairline borders came from `border-collapse: collapse`, so it is banned).
"""
import os
import re
from datetime import date

from lxml import etree

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged

_REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'report')
_PRINT_XML = os.path.join(_REPORT_DIR, 'nc_print.xml')
_TEMPLATES_XML = os.path.join(_REPORT_DIR, 'report_templates.xml')


@tagged('post_install', '-at_install')
class TestPrintSystem(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        recv = cls.company_data['default_account_receivable']
        rev = cls.company_data['default_account_revenue']
        journal = cls.company_data['default_journal_misc']
        for partner, amount, day in ((cls.partner_a, 1000.0, 5), (cls.partner_a, 250.5, 20),
                                     (cls.partner_b, 80.0, 9)):
            cls.env['account.move'].create({
                'move_type': 'entry', 'journal_id': journal.id, 'date': date(2026, 3, day),
                'line_ids': [
                    (0, 0, {'account_id': recv.id, 'debit': amount, 'credit': 0.0, 'partner_id': partner.id}),
                    (0, 0, {'account_id': rev.id, 'debit': 0.0, 'credit': amount})]}).action_post()
        cls.filters = {'date_from': date(2026, 1, 1), 'date_to': date(2026, 12, 31)}

    def _print(self, model, **extra):
        wiz = self.env[model].create(dict(self.filters, **extra))
        return wiz, wiz._nc_print_payload()

    # ----------------------------------------------------------- one design system for every report
    def test_every_engine_action_has_a_paper_and_uses_the_shared_body(self):
        tree = etree.parse(_TEMPLATES_XML)
        landscape = {'report_trial_balance', 'report_general_ledger', 'report_partner_ledger',
                     'report_aged_partner'}
        seen = 0
        for rec in tree.xpath("//record[@model='ir.actions.report']"):
            name = rec.xpath("field[@name='report_name']/text()")[0].split('.')[-1]
            paper = rec.xpath("field[@name='paperformat_id']/@ref")
            self.assertTrue(paper, '%s has no explicit paper' % name)
            expected = 'paperformat_nc_landscape' if name in landscape else 'paperformat_nc_portrait'
            self.assertEqual(paper[0], expected, name)
            seen += 1
        self.assertGreaterEqual(seen, 14)
        self.assertFalse(tree.xpath("//template[@id='report_financial']"),
                         'the shared body lives in nc_print.xml only')

    def test_the_layout_never_uses_the_odoo_boxed_external_layout(self):
        text = open(_PRINT_XML, encoding='utf-8').read()
        self.assertNotIn('web.external_layout', text)

    # ----------------------------------------------------------- wkhtmltopdf 0.12.6 limits
    def test_css_stays_inside_what_wkhtmltopdf_can_draw(self):
        """Banned: flexbox/grid/variables/logical properties (silently ignored by the patched Qt) and
        ``border-collapse: collapse`` (draws hairline outlines on every cell in the PDF backend)."""
        css = ''.join(etree.parse(_PRINT_XML).xpath("//template[@id='nc_print_css']//text()"))
        css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
        for banned in (r'display\s*:\s*(flex|grid|inline-flex)', r'var\(--', r'margin-inline', r'padding-inline',
                       r'border-collapse\s*:\s*collapse', r'inset-', r'\bisolate\b', r'unicode-bidi\s*:\s*isolate'):
            self.assertIsNone(re.search(banned, css), banned)
        self.assertIn('border-collapse: separate', css)

    def test_no_print_template_formats_with_a_percent_operator(self):
        tree = etree.parse(_PRINT_XML)
        for node in tree.iter():
            for name, value in node.attrib.items():
                if name.startswith('t-'):
                    self.assertIsNone(re.search(r"['\"]\s*%\s*[\(\w]", value), '%s=%r' % (name, value))

    def test_every_table_switches_the_border_attributes_off(self):
        for table in etree.parse(_PRINT_XML).xpath('//table'):
            self.assertEqual(table.get('cellspacing'), '0', etree.tostring(table)[:90])
            self.assertEqual(table.get('border'), '0')

    # ----------------------------------------------------------- the data side
    def test_widths_sum_to_one_hundred_percent(self):
        for model in ('ncollection.account.report.general.ledger', 'ncollection.account.report.trial.balance',
                      'ncollection.account.report.partner.ledger'):
            wiz, p = self._print(model)
            self.assertEqual(len(p['widths']), len(p['columns']), model)
            self.assertAlmostEqual(sum(p['widths']), 100.0, places=1, msg=model)

    def test_brand_colour_is_validated_and_defaults_safely(self):
        wiz = self.env['ncollection.account.report.trial.balance'].create(self.filters)
        brand = wiz._nc_print_brand()
        self.assertRegex(brand['color'], r'^#[0-9A-Fa-f]{6}$')
        for key in ('tint', 'line'):
            self.assertRegex(brand[key], r'^#[0-9A-F]{6}$')

    def test_the_print_layer_reuses_the_engine_rows(self):
        """Print and XLSX come from one set of lines: a default report's printed rows ARE the payload's."""
        wiz, p = self._print('ncollection.account.report.trial.balance')
        printed = sum(len(s['rows']) for s in p['sections'])
        self.assertEqual(printed, len(wiz._nc_export_payload()['rows']))

    def test_general_ledger_groups_by_account_with_a_total_that_matches_the_ledger(self):
        wiz, p = self._print('ncollection.account.report.general.ledger')
        self.assertTrue(p['sections'])
        for sec in p['sections']:
            self.assertTrue(sec['title'])
            self.assertEqual(sec['rows'][-1]['cls'], 'sub')
        recv = self.company_data['default_account_receivable'].display_name
        sec = next(s for s in p['sections'] if s['title'] == recv)
        self.assertAlmostEqual(sec['debit'], 1330.5)
        self.assertAlmostEqual(sec['closing'], sec['opening'] + sec['debit'] - sec['credit'])
        items = sum(1 for s in p['sections'] for r in s['rows'] if not r['cls'])
        self.assertEqual(p['summary'][0][1], str(items))

    def test_partner_ledger_prints_one_section_per_partner(self):
        wiz, p = self._print('ncollection.account.report.partner.ledger')
        titles = {s['title'] for s in p['sections']}
        self.assertTrue({self.partner_a.display_name, self.partner_b.display_name} <= titles)
        closing = dict(p['summary'][i][:2] for i in range(len(p['summary'])))
        self.assertIn(wiz.env._("Closing balance"), closing)

    def test_a_statement_is_one_document_per_partner_with_a_summary_that_adds_up(self):
        wiz = self.env['ncollection.account.report.statement'].create(
            dict(self.filters, partner_ids=[(6, 0, (self.partner_a | self.partner_b).ids)]))
        p = wiz._nc_print_payload()
        self.assertEqual(len(p['docs']), 2)
        doc = next(d for d in p['docs'] if d['party']['name'] == self.partner_a.display_name)
        summary = {label: text for label, text, _tone in doc['summary']}
        self.assertEqual(summary[wiz.env._("Total debit")], '1,250.50')
        self.assertEqual(summary[wiz.env._("Closing balance")], '1,250.50')
        self.assertEqual(doc['sections'][0]['rows'][-1]['cls'], 'grand')
        # the partner is the page heading, so it is not repeated in the meta block
        self.assertNotIn(self.partner_a.display_name, ' '.join(t for _l, t in p['meta']))

    # ----------------------------------------------------------- the rendered pages
    def _html(self, wizard, lang=None):
        action = self.env.ref(wizard._nc_report_action_ref()).with_context(
            force_report_rendering=True, **({'lang': lang} if lang else {}))
        out = action._render_qweb_html(action.report_name, [wizard.id])[0]
        return out.decode() if isinstance(out, bytes) else out

    def test_a_page_has_a_running_header_a_footer_with_page_numbers_and_a_repeating_thead(self):
        wiz = self.env['ncollection.account.report.general.ledger'].create(self.filters)
        html = self._html(wiz)
        self.assertIn('class="header"', html)
        self.assertIn('class="footer"', html)
        self.assertIn('class="topage"', html)
        self.assertIn('<thead>', html)
        self.assertIn(self.env.company.name, html)

    def test_a_statement_renders_one_layout_for_the_whole_run(self):
        """One header/footer for N partners: wkhtmltopdf's cost grows with headers x pages, and a run of
        every customer once never finished (it is in the REGRESSIONS ledger)."""
        wiz = self.env['ncollection.account.report.statement'].create(
            dict(self.filters, partner_ids=[(6, 0, (self.partner_a | self.partner_b).ids)]))
        html = self._html(wiz)
        self.assertEqual(html.count('class="header"'), 1)
        self.assertEqual(html.count('page-break-before: always'), 1)   # the second partner only
        self.assertIn(self.partner_a.display_name, html)
        self.assertIn(self.partner_b.display_name, html)

    def test_arabic_page_is_rtl_and_the_new_labels_are_translated(self):
        self.env['res.lang']._activate_lang('ar_001')
        wiz = self.env['ncollection.account.report.statement'].with_context(lang='ar_001').create(
            dict(self.filters, partner_ids=[(6, 0, self.partner_a.ids)]))
        html = self._html(wiz, lang='ar_001')
        self.assertIn('dir="rtl"', html)
        for english in ('Opening balance', 'Closing balance', 'Total debit', 'Total credit', 'Customer'):
            self.assertNotIn('>%s<' % english, html, 'untranslated: ' + english)

    # ----------------------------------------------------------- right-to-left safety
    def test_dates_are_left_to_right_islands_so_arabic_pages_keep_their_order(self):
        wiz, p = self._print('ncollection.account.report.trial.balance')
        for text in (p['period'], p['generated']):
            self.assertTrue(text.startswith('‪') and text.endswith('‬'), repr(text))
        self.assertIn('→', p['period'])
        self.assertIn(p['period'], [t for _l, t in p['meta']])

    def test_bidi_marks_never_reach_the_xlsx(self):
        """The print layer decorates dates for a right-to-left PAGE; a spreadsheet cell must stay clean."""
        import base64
        import io
        import zipfile
        for model in ('ncollection.account.report.trial.balance', 'ncollection.account.report.statement'):
            wiz = self.env[model].create(self.filters)
            wiz._nc_print_payload()
            book = zipfile.ZipFile(io.BytesIO(base64.b64decode(wiz._nc_build_xlsx())))
            for name in book.namelist():
                if name.endswith('.xml'):
                    self.assertNotIn('‪'.encode(), book.read(name), name)

    def test_mixed_arabic_and_english_data_prints_in_both_directions(self):
        name = 'شركة النور General Trading ذ.م.م'
        partner = self.env['res.partner'].create({'name': name})
        recv = self.company_data['default_account_receivable']
        rev = self.company_data['default_account_revenue']
        label = 'فاتورة INV/2026/9001 – دفعة (50%) Tower-B ' + 'وصف طويل ' * 20
        self.env['account.move'].create({
            'move_type': 'entry', 'journal_id': self.company_data['default_journal_misc'].id,
            'date': date(2026, 4, 1), 'line_ids': [
                (0, 0, {'account_id': recv.id, 'partner_id': partner.id, 'name': label, 'debit': 10.0}),
                (0, 0, {'account_id': rev.id, 'name': label, 'credit': 10.0})]}).action_post()
        self.env['res.lang']._activate_lang('ar_001')
        for lang in (None, 'ar_001'):
            wiz = self.env['ncollection.account.report.statement'].with_context(lang=lang).create(
                dict(self.filters, partner_ids=[(6, 0, partner.ids)]))
            html = self._html(wiz, lang=lang)
            self.assertIn(name, html)
            self.assertIn('INV/2026/9001', html)

    # ----------------------------------------------------------- access
    def test_a_user_without_accounting_rights_cannot_build_or_print_a_statement(self):
        from odoo.exceptions import AccessError
        user = self.env['res.users'].create({
            'name': 'No accounting', 'login': 'noacct_print', 'company_id': self.env.company.id,
            'company_ids': [(6, 0, self.env.company.ids)],
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        with self.assertRaises(AccessError):
            self.env['ncollection.account.report.statement'].with_user(user).create(self.filters)

    def test_print_meta_never_has_an_empty_value(self):
        wiz = self.env['ncollection.account.report.statement'].create(
            dict(self.filters, partner_ids=[(6, 0, self.partner_a.ids)]))
        payload = wiz._nc_print_payload()
        for label, text in payload['meta']:
            self.assertTrue(text, 'empty meta value for %r' % label)
