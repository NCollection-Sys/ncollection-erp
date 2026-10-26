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
        cls.company_data_2 = cls.setup_other_company()      # a second company with its own chart

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
        action = wizard.env.ref(wizard._nc_report_action_ref()).with_context(
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
            html = self._html(wiz, lang=lang).replace('‪', '').replace('‬', '')   # bidi runs
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

    # ----------------------------------------------------------- company access
    def _company_b_run(self):
        """A posted entry in the second company, and a user who belongs to the FIRST company only."""
        data_b = self.company_data_2
        self.company_b = data_b['company']
        self.partner_b_only = self.env['res.partner'].create({
            'name': 'Company B Only Customer', 'company_id': self.company_b.id})
        self.env['account.move'].with_company(self.company_b).create({
            'move_type': 'entry', 'journal_id': data_b['default_journal_misc'].id, 'date': date(2026, 3, 3),
            'line_ids': [
                (0, 0, {'account_id': data_b['default_account_receivable'].id, 'debit': 777.0,
                        'partner_id': self.partner_b_only.id}),
                (0, 0, {'account_id': data_b['default_account_revenue'].id, 'credit': 777.0})]}).action_post()
        groups = [self.env.ref('base.group_user').id, self.env.ref('account.group_account_readonly').id]
        self.user_a = self.env['res.users'].create({
            'name': 'A only', 'login': 'company_a_only_print', 'company_id': self.env.company.id,
            'company_ids': [(6, 0, self.env.company.ids)], 'group_ids': [(6, 0, groups)]})
        self.user_ab = self.env['res.users'].create({
            'name': 'A and B', 'login': 'company_ab_print', 'company_id': self.env.company.id,
            'company_ids': [(6, 0, (self.env.company | self.company_b).ids)], 'group_ids': [(6, 0, groups)]})

    def test_a_user_of_company_a_cannot_print_company_b(self):
        """Creating the wizard directly with company B (bypassing the service) is refused outright, so
        neither B's letterhead (name, address, tax id, logo) nor B's figures can be printed. This is a
        constraint on the engine, not only a check in `nc_report_run`."""
        from odoo.exceptions import AccessError
        self._company_b_run()
        for model in ('ncollection.account.report.general.ledger', 'ncollection.account.report.statement',
                      'ncollection.account.report.trial.balance'):
            with self.assertRaises(AccessError, msg=model):
                self.env[model].with_user(self.user_a).create(dict(self.filters, company_id=self.company_b.id))
            wiz = self.env[model].with_user(self.user_a).create(dict(self.filters))      # its own company: fine
            self.assertEqual(wiz.company_id, self.env.company)
            with self.assertRaises(AccessError, msg=model):                              # nor by editing it later
                wiz.write({'company_id': self.company_b.id})
            html = self._html(wiz)
            self.assertNotIn('Company B Only Customer', html, model)
            self.assertNotIn('777.00', html, model)
            self.assertNotIn(self.company_b.name, html, model)

    def test_a_user_of_both_companies_prints_each_company_with_its_own_letterhead(self):
        self._company_b_run()
        wiz = self.env['ncollection.account.report.statement'].with_user(self.user_ab).with_context(
            allowed_company_ids=(self.env.company | self.company_b).ids).create(
            dict(self.filters, company_id=self.company_b.id, partner_ids=[(6, 0, self.partner_b_only.ids)]))
        payload = wiz._nc_print_payload()
        self.assertEqual(payload['co']['name'], self.company_b.name)
        self.assertEqual(payload['docs'][0]['party']['name'], 'Company B Only Customer')
        self.assertEqual(payload['docs'][0]['summary'][1][1], '777.00')

    # ----------------------------------------------------------- the page prints the payload, nothing else
    def test_the_rendered_page_prints_exactly_the_payload_cells_including_percentages(self):
        """Trial Balance, Balance Sheet and P&L: every cell of every row (values, section totals, grand
        total, zero rows and the variance percentages) is printed as the prepared string, in order."""
        from lxml import html as lhtml
        for model in ('ncollection.account.report.trial.balance', 'ncollection.account.report.balance.sheet',
                      'ncollection.account.report.profit.loss'):
            vals = dict(self.filters)
            if model.endswith(('balance.sheet', 'profit.loss')):
                vals['comparison_type'] = 'previous_period'
            wiz = self.env[model].create(vals)
            payload = wiz._nc_print_payload()
            page = lhtml.fromstring(self._html(wiz))
            printed = [[''.join(td.itertext()).strip() for td in tr.findall('td')]
                       for tr in page.xpath("//table[contains(@class,'nc-t')]/tbody/tr")]
            expected = [[c['text'] for c in row['cells']] for s in payload['sections'] for row in s['rows']]
            self.assertEqual(printed, expected, model)
            if 'comparison_type' in vals:
                self.assertTrue(any(t.endswith('%') for r in expected for t in r), 'no % cell: ' + model)
            # and the payload rows ARE the engine rows (headings only lose their muted zero)
            engine = wiz._nc_export_payload()['rows']
            self.assertEqual(len(expected), len(engine), model)
            for mine, theirs, row in zip(expected, engine, [r for s in payload['sections'] for r in s['rows']]):
                for text, cell in zip(mine, theirs['cells']):
                    if row['cls'] == 'section' and not text:
                        continue                      # a heading's blank where the engine has 0
                    self.assertEqual(text, cell['text'], (model, theirs))

    def test_latin_phrases_in_arabic_text_are_left_to_right_runs(self):
        bidi = self.env['ncollection.account.report']._nc_bidi
        self.assertEqual(bidi('الإمارات Trading LLC (12)'), 'الإمارات ‪Trading LLC (12)‬')
        self.assertEqual(bidi('دفعة (50%) لمشروع Tower-B'), 'دفعة ‪(50%)‬ لمشروع ‪Tower-B‬')
        self.assertEqual(bidi('شركة (فرع)'), 'شركة (فرع)')            # an Arabic bracket is left alone
        self.assertEqual(bidi('‪01/01/2026‬'), '‪01/01/2026‬')     # no nesting
        self.assertEqual(bidi(''), '')

    def test_english_pages_are_not_decorated_with_bidi_marks(self):
        wiz = self.env['ncollection.account.report.general.ledger'].create(self.filters)
        payload = wiz._nc_print_payload()
        self.assertNotIn('‪', str(payload['sections']))

    def test_the_arabic_letterhead_and_party_details_are_bidi_safe_too(self):
        self.env.company.name = 'Al Barari Trading LLC (Dubai)'
        self.env['res.lang']._activate_lang('ar_001')
        wiz = self.env['ncollection.account.report.statement'].with_context(lang='ar_001').create(
            dict(self.filters, partner_ids=[(6, 0, self.partner_a.ids)]))
        payload = wiz._nc_print_payload()
        self.assertEqual(payload['co']['name'], '‪Al Barari Trading LLC (Dubai)‬')
        self.assertTrue(payload['docs'])
        english = self.env['ncollection.account.report.statement'].create(
            dict(self.filters, partner_ids=[(6, 0, self.partner_a.ids)]))._nc_print_payload()
        self.assertEqual(english['co']['name'], 'Al Barari Trading LLC (Dubai)')   # left-to-right pages untouched

    def test_every_report_model_refuses_a_company_the_user_is_not_in(self):
        """A report model that overrode `create` without calling super() would silently skip the check."""
        from odoo.exceptions import AccessError
        self._company_b_run()
        engine = type(self.env['ncollection.account.report'])
        names = sorted(
            name for name, model in self.env.registry.items()
            if name.startswith('ncollection.account.report.') and issubclass(model, engine)
            and not self.env[name]._abstract and hasattr(self.env[name], '_nc_columns')
            and not name.endswith('.line'))
        self.assertGreaterEqual(len(names), 15, names)
        for name in names:
            with self.assertRaises(AccessError, msg=name):
                self.env[name].with_user(self.user_a).create(dict(self.filters, company_id=self.company_b.id))

    def test_the_report_header_prints_no_country_line(self):
        self.env.company.partner_id.write({'country_id': self.env.ref('base.us').id, 'street': '1 Ledger Street'})
        for model in ('ncollection.account.report.trial.balance', 'ncollection.account.report.general.ledger'):
            with self.subTest(model=model):
                _wiz, p = self._print(model)
                self.assertIn('1 Ledger Street', p['co']['lines'])
                self.assertNotIn('United States', ' '.join(p['co']['lines']))
