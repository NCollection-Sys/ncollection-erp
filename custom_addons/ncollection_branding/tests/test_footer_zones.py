# -*- coding: utf-8 -*-
"""Three footer zones: every company layout prints Left | Center | Right in physical order when a zone is set, and
exactly its old single Footer otherwise; never both. Rendered through Odoo's own layout preview (the same templates
the PDF uses, with the company record)."""
from lxml import html as lhtml

from odoo.tests import TransactionCase, tagged
from odoo.tools import is_html_empty

LAYOUTS = ('web.external_layout_standard', 'web.external_layout_boxed', 'web.external_layout_bold',
           'web.external_layout_striped', 'web.external_layout_folder', 'web.external_layout_wave',
           'web.external_layout_bubble', 'ncollection_branding.external_layout_nc')


@tagged('post_install', '-at_install')
class TestFooterZones(TransactionCase):

    def _render(self, layout_xmlid, lang='en_US'):
        company = self.env.company.with_context(lang=lang)
        view = self.env.ref(layout_xmlid)
        report_layout = self.env['report.layout'].search([('view_id', '=', view.id)], limit=1)
        if not report_layout:
            self.env['report.layout'].create({'name': view.name, 'view_id': view.id})
        company.external_report_layout_id = view
        body = self.env['ir.ui.view'].with_context(lang=lang)._render_template(
            'web.report_invoice_wizard_preview', {'company': company, 'preview_css': '', 'is_html_empty': is_html_empty})
        return lhtml.fromstring(str(body))

    def test_zones_replace_the_single_footer_in_every_layout(self):
        self.env.company.write({'report_footer': '<p>LEGACY-FOOTER</p>', 'nc_footer_left': '<p>LEFT-ZONE</p>',
                                'nc_footer_center': False, 'nc_footer_right': '<p>RIGHT-ZONE</p>'})
        for xmlid in LAYOUTS:
            with self.subTest(layout=xmlid):
                doc = self._render(xmlid)
                tables = doc.xpath("//table[contains(@class, 'o_nc_footer_zones')]")
                self.assertEqual(len(tables), 1, "one zones row")
                cells = tables[0].xpath('./tr/td | ./tbody/tr/td')
                self.assertEqual([c.get('style').split('text-align: ')[1].split(';')[0] for c in cells],
                                 ['left', 'center', 'right'], "physical order")
                self.assertIn('LEFT-ZONE', cells[0].text_content())
                self.assertEqual(cells[1].text_content().strip(), '', "an empty zone stays empty")
                self.assertIn('RIGHT-ZONE', cells[2].text_content())
                self.assertNotIn('LEGACY-FOOTER', doc.text_content(), "never printed twice")

    def test_no_zone_keeps_the_legacy_footer(self):
        self.env.company.write({'report_footer': '<p>LEGACY-FOOTER</p>', 'nc_footer_left': False,
                                'nc_footer_center': False, 'nc_footer_right': False})
        for xmlid in LAYOUTS:
            with self.subTest(layout=xmlid):
                doc = self._render(xmlid)
                self.assertFalse(doc.xpath("//table[contains(@class, 'o_nc_footer_zones')]"))
                self.assertIn('LEGACY-FOOTER', doc.text_content())

    def test_arabic_keeps_physical_positions_and_text_direction(self):
        self.env['res.lang']._activate_lang('ar_001')
        company = self.env.company
        company.write({'nc_footer_left': '<p>LEFT</p>', 'nc_footer_center': '<p>CENTER</p>',
                       'nc_footer_right': '<p>RIGHT</p>'})
        company.with_context(lang='ar_001').nc_footer_right = '<p>الرقم الضريبي</p>'
        doc = self._render('web.external_layout_standard', lang='ar_001')
        table = doc.xpath("//table[contains(@class, 'o_nc_footer_zones')]")[0]
        self.assertEqual(table.get('dir'), 'ltr', "Left is the left of the page in Arabic too")
        right = table.xpath(".//div[contains(@class, 'o_nc_footer_right')]")[0]
        self.assertEqual(right.get('dir'), 'auto')
        self.assertIn('الرقم الضريبي', right.text_content())

    def test_each_company_has_its_own_zones(self):
        other = self.env['res.company'].create({'name': 'Footer Co Two'})
        self.env.company.nc_footer_center = '<p>MINE</p>'
        self.assertFalse(other.nc_footer_center)
        self.assertNotIn('MINE', str(other.nc_footer_center or ''))

    def test_the_layout_wizard_edits_the_zones_and_previews_them(self):
        wizard = self.env['base.document.layout'].create({'company_id': self.env.company.id})
        wizard.nc_footer_center = '<p>WIZARD-CENTER</p>'
        self.assertEqual(str(self.env.company.nc_footer_center), '<p>WIZARD-CENTER</p>')
        if wizard.report_layout_id:
            self.assertIn('WIZARD-CENTER', str(wizard.preview))
