# -*- coding: utf-8 -*-
"""Print Templates: how each kind of printed document LOOKS, set by an administrator, read by the one print pipeline.

Every printable document already has a ``ncollection.document.type`` record (shipped as data by the module that prints
it: invoices, bills, credit notes, receipts, quotations, orders, statements...). That record now also carries its
template: title, header and footer text, logo, colours, typography, page (size, orientation, margins, numbering),
which sections and columns print, and the language / direction behaviour. Nothing here renders: the NCollection
document layout (views/report_layout_templates.xml, ``external_layout_nc``) asks ``_nc_print_options(record)`` for the
options of the document it is printing, and ``ir.actions.report.get_paperformat`` asks for its page. Preview, View and
Print are the same ``_render_qweb_pdf`` call, so one setting reaches all three. A new kind of document plugs in by
shipping a document type record for its report; nothing else.

Who may change it: the same rule as the paper format and the stamps (``_nc_check_print_settings_allowed``).
"""
import ast
import re

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

_HEX = re.compile(r'^#[0-9A-Fa-f]{6}$')
_PAGE_FIELDS = ('nc_paper_size', 'nc_orientation', 'nc_margin_top', 'nc_margin_bottom', 'nc_margin_left',
                'nc_margin_right')
# Font stacks the PDF engine has (wkhtmltopdf in the server image), never a free-text CSS value.
FONT_STACKS = {
    'default': "",
    'sans': "'DejaVu Sans', Arial, sans-serif",
    'serif': "'DejaVu Serif', Georgia, serif",
    'mono': "'DejaVu Sans Mono', monospace",
}
# Column switch -> the cells Odoo's own document templates name (invoice, sale and purchase use the same names).
COLUMNS = {
    'nc_show_quantity': ('th_quantity', 'td_quantity'),
    'nc_show_unit_price': ('th_priceunit', 'td_priceunit', 'td_price_unit'),
    'nc_show_discount': ('th_discount', 'td_discount'),
    'nc_show_taxes': ('th_taxes', 'td_taxes'),
}


class NcDocumentPrintTemplate(models.Model):
    _inherit = 'ncollection.document.type'

    # ---- template & identity
    nc_title = fields.Char(
        string='Document title', translate=True,
        help="Printed as the document's title instead of the standard one (for example \"Tax Invoice\"). The number "
             "of the document follows it. Empty: the standard title.")
    nc_header_text = fields.Text(
        string='Header text', translate=True,
        help="A line printed under the company's details at the top of every page.")
    nc_footer_text = fields.Text(
        string='Footer text', translate=True,
        help="Printed at the bottom of every page, instead of the company's report footer. Empty: the company footer.")
    nc_logo_source = fields.Selection([
        ('company', "Company logo"),
        ('system', "System logo"),
        ('custom', "This document's own logo"),
        ('none', "No logo (company name)"),
    ], string='Logo', default='company', required=True)
    nc_logo = fields.Image(string='Document logo', max_width=600, max_height=300)
    nc_show_company_details = fields.Boolean(
        string='Company details', default=True,
        help="The company's address, tax number, phone and email in the header.")
    # ---- colours & typography
    nc_primary_color = fields.Char(
        string='Accent colour', help="Title, rules and totals. Empty: the company's document colour.")
    nc_text_color = fields.Char(string='Text colour', help="Body text. Empty: dark grey.")
    nc_font = fields.Selection([
        ('default', "Default"), ('sans', "Sans-serif"), ('serif', "Serif"), ('mono', "Monospace"),
    ], string='Font', default='default', required=True)
    nc_font_size = fields.Float(string='Text size (pt)', help="0: the layout's own size (larger in Arabic).")
    # ---- page
    nc_paper_size = fields.Selection([('A4', 'A4'), ('A5', 'A5'), ('Letter', 'Letter'), ('Legal', 'Legal')],
                                     string='Paper size')
    nc_orientation = fields.Selection([('Portrait', 'Portrait'), ('Landscape', 'Landscape')], string='Orientation')
    nc_margin_top = fields.Float(string='Top margin (mm)', default=34)
    nc_margin_bottom = fields.Float(string='Bottom margin (mm)', default=18)
    nc_margin_left = fields.Float(string='Left margin (mm)', default=12)
    nc_margin_right = fields.Float(string='Right margin (mm)', default=12)
    nc_page_numbers = fields.Boolean(string='Page numbers', default=True)
    # ---- sections & columns
    nc_show_customer_address = fields.Boolean(string='Customer address block', default=True)
    nc_show_payment_reference = fields.Boolean(string='Payment reference', default=True)
    nc_show_stamp = fields.Boolean(
        string='Document stamp', default=True, help="Apply the Document Stamps that match this document.")
    nc_show_quantity = fields.Boolean(string='Quantity column', default=True)
    nc_show_unit_price = fields.Boolean(string='Unit price column', default=True)
    nc_show_discount = fields.Boolean(string='Discount column', default=True)
    nc_show_taxes = fields.Boolean(string='Taxes column', default=True)
    # ---- language & direction
    nc_language_mode = fields.Selection([
        ('bilingual', "Bilingual where the template provides it (English and Arabic)"),
        ('single', "Document language only"),
    ], string='Labels', default='bilingual', required=True)
    nc_direction = fields.Selection([
        ('auto', "Follow the document language"), ('ltr', "Left to right"), ('rtl', "Right to left"),
    ], string='Direction', default='auto', required=True)

    @api.model
    def _nc_print_setting_fields(self):
        return super()._nc_print_setting_fields() | {
            'nc_title', 'nc_header_text', 'nc_footer_text', 'nc_logo_source', 'nc_logo', 'nc_show_company_details',
            'nc_primary_color', 'nc_text_color', 'nc_font', 'nc_font_size', 'nc_page_numbers',
            'nc_show_customer_address', 'nc_show_payment_reference', 'nc_show_stamp', 'nc_language_mode',
            'nc_direction', *_PAGE_FIELDS, *COLUMNS}

    @api.constrains('nc_primary_color', 'nc_text_color', 'nc_font_size', *_PAGE_FIELDS)
    def _check_print_template(self):
        for kind in self:
            for color in (kind.nc_primary_color, kind.nc_text_color):
                if color and not _HEX.fullmatch(color):
                    raise ValidationError(self.env._("A colour is a 6-digit hex value such as #1F5F8F."))
            if kind.nc_font_size and not 6 <= kind.nc_font_size <= 16:
                raise ValidationError(self.env._("The text size must be between 6 and 16 pt."))
            for margin in (kind.nc_margin_top, kind.nc_margin_bottom, kind.nc_margin_left, kind.nc_margin_right):
                if not 0 <= (margin or 0) <= 80:
                    raise ValidationError(self.env._("A margin must be between 0 and 80 mm."))

    # ------------------------------------------------------------------ page
    def write(self, vals):
        result = super().write(vals)
        if set(vals) & set(_PAGE_FIELDS) and not self.env.context.get('nc_page_sync'):
            for kind in self:
                kind._nc_sync_page()
        return result

    def _nc_sync_page(self):
        """The page fields become this kind's own paper format (created once, then kept in step). A size and an
        orientation are both needed for a page of its own; clearing them returns to the shared document paper."""
        self.ensure_one()
        Paper = self.env['report.paperformat'].sudo()
        if not (self.nc_paper_size and self.nc_orientation):
            return
        vals = {
            'name': self.env._("Print template: %s", self.name), 'format': self.nc_paper_size,
            'orientation': self.nc_orientation, 'margin_top': self.nc_margin_top,
            'margin_bottom': self.nc_margin_bottom, 'margin_left': self.nc_margin_left,
            'margin_right': self.nc_margin_right, 'header_line': False, 'dpi': 90,
            'header_spacing': max((self.nc_margin_top or 0) - 5, 0),
        }
        paper = self.sudo().paperformat_id
        own = paper and paper.name == vals['name']
        if own:
            paper.write(vals)
        else:
            paper = Paper.create(vals)
        if self.paperformat_id != paper:
            self.with_context(nc_page_sync=True).write({'paperformat_id': paper.id})

    # ------------------------------------------------------------------ what the layout reads
    @api.model
    def _nc_kind_of(self, record):
        """The document type ``record`` prints as (the first by sequence whose report prints its model and whose
        condition holds), or an empty recordset."""
        if not record:
            return self.browse()
        kinds = self.sudo().search([('report_ids.model', '=', record._name)])
        return kinds.filtered(lambda kind: kind._nc_matches(record))[:1]

    @api.model
    def _nc_print_options(self, record, company=None):
        """Everything ``external_layout_nc`` needs to print ``record`` the way its Print Template says. Values are
        validated here (colours, fonts, column names), so the template only interpolates trusted strings."""
        kind = self._nc_kind_of(record)
        company = company or (record.company_id if record and 'company_id' in record._fields else self.env.company)
        logo = False
        source = kind.nc_logo_source if kind else 'company'
        if source == 'company':
            logo = company.logo
        elif source == 'custom':
            logo = kind.nc_logo or company.logo
        elif source == 'system':
            brand = self.env['ncollection.product.branding'].sudo()._nc_record()
            logo = brand.logo if brand and 'logo' in brand._fields else company.logo
        hidden = []
        if kind:
            for flag, names in COLUMNS.items():
                if not kind[flag]:
                    hidden += names
        accent = kind.nc_primary_color if kind and kind.nc_primary_color else company._nc_document_color()
        partner = company.partner_id
        city = ' '.join(x for x in (partner.zip, partner.city, partner.state_id.name) if x)
        # The company's address WITHOUT a country line: Odoo seeds the main company as United States, and a country
        # alone under the name says nothing a reader needs (the same rule as the financial reports' header).
        company_lines = [x for x in (partner.street, partner.street2, city) if x]
        return {
            'kind': kind,
            'title': (kind.nc_title or '').strip() if kind else '',
            'header_text': kind.nc_header_text if kind else '',
            'footer_text': kind.nc_footer_text if kind else '',
            'logo': logo,
            'show_company_details': kind.nc_show_company_details if kind else True,
            'accent': accent if _HEX.fullmatch(accent or '') else '#1F5F8F',
            'text': kind.nc_text_color if kind and _HEX.fullmatch(kind.nc_text_color or '') else '',
            'font': FONT_STACKS.get(kind.nc_font if kind else 'default', ''),
            'font_size': kind.nc_font_size if kind and kind.nc_font_size else 0,
            'page_numbers': kind.nc_page_numbers if kind else True,
            'show_address': kind.nc_show_customer_address if kind else True,
            'show_payment_reference': kind.nc_show_payment_reference if kind else True,
            'single_language': bool(kind and kind.nc_language_mode == 'single'),
            'direction': kind.nc_direction if kind else 'auto',
            'hidden_cells': hidden,
            'company_lines': company_lines,
        }

    # ------------------------------------------------------------------ preview
    def action_nc_preview(self):
        """Open the most recent document of this kind exactly as Print produces it (the same report, the same
        settings, the same renderer). A setting saved here shows in the next preview and in every print."""
        self.ensure_one()
        report = self.report_ids[:1]
        if not report:
            raise UserError(self.env._("%s has no report to preview.", self.name))
        Model = self.env[report.model]
        domain = ast.literal_eval(self.record_domain or '[]')
        sample = Model.search(domain, limit=1, order='id desc')
        if not sample:
            raise UserError(self.env._(
                "There is no %s yet to preview. Create one, and its preview shows these settings.", self.name))
        if hasattr(sample, '_nc_print_report'):
            # the report its View and Print use (nc.document.print.mixin), so Preview cannot pick another one
            report = sample._nc_print_report() or report
        return {'type': 'ir.actions.act_url', 'target': 'new',
                'url': '/report/pdf/%s/%s' % (report.report_name, sample.id)}
