# -*- coding: utf-8 -*-
"""Request 06: Print Settings of ONE invoice / bill / credit note.

The Print Template of the document's kind (``ncollection.document.type``, ncollection_branding) and the company's
Configure Document Layout stay the DEFAULTS. A document may override some of them in its own
``ncollection.move.print.setting`` (one row per move, created on first save, deleted by Reset to Company Defaults).
Nothing here writes ``res.company``, ``base.document.layout`` or a document type: one invoice's settings never reach
another invoice or the company.

How it reaches the PDF: the one print pipeline asks ``ncollection.document.type._nc_print_options(record)``; for an
``account.move`` that answer is overlaid with this row (``_nc_apply``). The page (paper / orientation) travels in
the print context (``account.move._nc_print_context``), which ``ir.actions.report.get_paperformat`` honours, so
Preview, View and Print are still the same render. "Paid on" lines are Odoo's own (``print_with_payments`` of
``account.report_invoice_document``) from the invoice's real reconciled payments; the switch only shows or hides that
section, it never changes a payment or a state.

Who may change it: whoever may write the document (checked on every create / write / unlink). The Document Stamp
switch keeps its own stricter rule (``nc.document.stamp.mixin``): it is written through to the move.
"""
import re

from odoo import api, fields, models
from odoo.exceptions import ValidationError

_HEX = re.compile(r'^#[0-9A-Fa-f]{6}$')
TRI = [('default', 'Default'), ('show', 'Show'), ('hide', 'Hide')]
# switch -> key of _nc_print_options
SECTION_KEYS = {'show_customer_address': 'show_address', 'show_payment_reference': 'show_payment_reference'}
COLUMN_CELLS = {
    'show_quantity': ('th_quantity', 'td_quantity'),
    'show_unit_price': ('th_priceunit', 'td_priceunit', 'td_price_unit'),
    'show_discount': ('th_discount', 'td_discount'),
    'show_taxes': ('th_taxes', 'td_taxes'),
}


class MovePrintSetting(models.Model):
    _name = 'ncollection.move.print.setting'
    _description = 'Print Settings of one accounting document'
    _check_company_auto = True

    move_id = fields.Many2one('account.move', required=True, ondelete='cascade', index=True, readonly=True)
    company_id = fields.Many2one(related='move_id.company_id', store=True, index=True)
    # ---- layout (empty = the document type / company default)
    template_id = fields.Many2one(
        'ncollection.document.type', string='Print Template', domain="[('report_ids.model', '=', 'account.move')]",
        help="Print this document with another Print Template of invoices (its colours, sections and columns).")
    title = fields.Char(help="Empty: the template's title.")
    header_text = fields.Text(string='Tagline / header text')
    footer_text = fields.Text(string='Footer text')
    show_company_details = fields.Selection(TRI, string='Company address', default='default', required=True)
    primary_color = fields.Char(string='Accent colour')
    text_color = fields.Char(string='Text colour')
    font = fields.Selection([('default', 'Template font'), ('sans', 'Sans-serif'), ('serif', 'Serif'),
                             ('mono', 'Monospace')], default='default', required=True)
    font_size = fields.Float(string='Text size (pt)', help="0: the template's size.")
    paper_size = fields.Selection([('A4', 'A4'), ('A5', 'A5'), ('Letter', 'Letter'), ('Legal', 'Legal')])
    orientation = fields.Selection([('Portrait', 'Portrait'), ('Landscape', 'Landscape')])
    # ---- sections & columns
    show_customer_address = fields.Selection(TRI, string='Customer address', default='default', required=True)
    show_payment_reference = fields.Selection(TRI, string='Payment reference', default='default', required=True)
    show_quantity = fields.Selection(TRI, string='Quantity column', default='default', required=True)
    show_unit_price = fields.Selection(TRI, string='Unit price column', default='default', required=True)
    show_discount = fields.Selection(TRI, string='Discount column', default='default', required=True)
    show_taxes = fields.Selection(TRI, string='Taxes column', default='default', required=True)
    show_bank_details = fields.Selection(
        TRI, string='Bank details', default='default', required=True,
        help="The receiving bank account and the payment QR code printed with the payment communication.")
    show_payment_terms = fields.Selection(TRI, string='Payment terms', default='default', required=True)
    show_notes = fields.Selection(TRI, string='Notes', default='default', required=True,
                                  help="The invoice's Terms and Conditions / notes.")
    stamp_mode = fields.Selection(related='move_id.nc_stamp_mode', readonly=False, string='Document Stamp')
    stamp_editable = fields.Boolean(related='move_id.nc_stamp_mode_editable')
    stamp_default_on = fields.Boolean(related='move_id.nc_stamp_default_on')
    move_type = fields.Selection(related='move_id.move_type')
    # ---- payment details
    show_payment_history = fields.Selection(
        TRI, string='Payment history', default='default', required=True,
        help="'Paid on <date> - <amount>' lines from the invoice's reconciled payments (Odoo's own). Default: as "
             "the report prints them. Hiding them changes nothing in the accounting.")

    _move_unique = models.Constraint('UNIQUE(move_id)', 'An accounting document has one set of Print Settings.')

    @api.constrains('template_id')
    def _check_template(self):
        for rec in self.filtered('template_id'):
            if 'account.move' not in rec.template_id.sudo().report_ids.mapped('model'):
                raise ValidationError(self.env._("%s is not a Print Template of invoices.",
                                                 rec.template_id.display_name))

    @api.constrains('primary_color', 'text_color', 'font_size')
    def _check_values(self):
        for rec in self:
            for color in (rec.primary_color, rec.text_color):
                if color and not _HEX.fullmatch(color):
                    raise ValidationError(self.env._("A colour is a 6-digit hex value such as #1F5F8F."))
            if rec.font_size and not 6 <= rec.font_size <= 16:
                raise ValidationError(self.env._("The text size must be between 6 and 16 pt."))
            if bool(rec.paper_size) != bool(rec.orientation):
                raise ValidationError(self.env._("Choose both the paper size and the orientation, or neither."))

    # ------------------------------------------------------------------ access: the document's own write right
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.move_id.check_access('write')
        return records

    def write(self, vals):
        if 'move_id' in vals and any(rec.move_id.id != vals['move_id'] for rec in self):
            raise ValidationError(self.env._("Print Settings belong to their document."))
        self.move_id.check_access('write')
        return super().write(vals)

    def unlink(self):
        self.move_id.check_access('write')
        return super().unlink()

    # ------------------------------------------------------------------ what the print pipeline reads
    def _nc_apply(self, options):
        """Overlay these settings on the template's ``options`` (a copy is returned; values validated)."""
        self.ensure_one()
        from odoo.addons.ncollection_branding.models.document_print_template import FONT_STACKS
        opts = dict(options)
        if self.title:
            opts['title'] = self.title.strip()
        if self.header_text:
            opts['header_text'] = self.header_text
        if self.footer_text:
            opts['footer_text'] = self.footer_text
        if self.show_company_details != 'default':
            opts['show_company_details'] = self.show_company_details == 'show'
        if self.primary_color and _HEX.fullmatch(self.primary_color):
            opts['accent'] = self.primary_color
        if self.text_color and _HEX.fullmatch(self.text_color):
            opts['text'] = self.text_color
        if self.font != 'default':
            opts['font'] = FONT_STACKS.get(self.font, '')
        if self.font_size:
            opts['font_size'] = self.font_size
        for name, key in SECTION_KEYS.items():
            if self[name] != 'default':
                opts[key] = self[name] == 'show'
        hidden = list(opts.get('hidden_cells') or [])
        for name, cells in COLUMN_CELLS.items():
            if self[name] == 'hide':
                hidden += [cell for cell in cells if cell not in hidden]
            elif self[name] == 'show':
                hidden = [cell for cell in hidden if cell not in cells]
        opts['hidden_cells'] = hidden
        return opts

    def _nc_paperformat(self):
        """A page of its own for this document (created once per size / orientation, shared by every document that
        asks for the same page), or False for the template's page."""
        self.ensure_one()
        if not (self.paper_size and self.orientation):
            return False
        Paper = self.env['report.paperformat'].sudo()
        name = 'NCollection document %s %s' % (self.paper_size, self.orientation)
        paper = Paper.search([('name', '=', name)], limit=1)
        if not paper:
            base = self.env.ref('ncollection_branding.paperformat_nc_document', raise_if_not_found=False)
            vals = {'name': name, 'format': self.paper_size, 'orientation': self.orientation, 'dpi': 90,
                    'header_line': False}
            if base:
                vals.update({f: base[f] for f in ('margin_top', 'margin_bottom', 'margin_left', 'margin_right',
                                                  'header_spacing')})
            paper = Paper.create(vals)
        return paper

    # ------------------------------------------------------------------ dialog actions
    def action_save(self):
        return {'type': 'ir.actions.act_window_close'}

    def action_preview(self):
        """The very PDF Print produces, with these settings."""
        self.ensure_one()
        return self.move_id.action_nc_view()

    def action_reset(self):
        """Reset to Company Defaults: this document follows its Print Template and the company layout again."""
        move = self.move_id
        self.unlink()
        if move.nc_stamp_mode != 'default' and move.nc_stamp_mode_editable:
            move.nc_stamp_mode = 'default'
        return {'type': 'ir.actions.act_window_close'}
