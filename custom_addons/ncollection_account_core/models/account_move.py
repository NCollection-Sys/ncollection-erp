# -*- coding: utf-8 -*-
"""Custom print title of ONE invoice.

Presentation only: two plain fields on ``account.move`` and a template that prints them instead of the stock
"Invoice" / "Credit Note" / ... heading. Nothing here touches the number, the state, posting, journal items or
any accounting field, and the stock title stays the default (switch off, or leave empty, and the document
prints exactly as before). The title belongs to the individual document: it is not a company or journal
setting, and it is not copied to a duplicated invoice or to its credit note.
"""
from odoo import api, fields, models
from odoo.exceptions import ValidationError

NC_PRINT_TITLE_MAX = 80


class AccountMove(models.Model):
    _inherit = 'account.move'

    nc_print_title_enabled = fields.Boolean(
        string='Custom print title', copy=False, default=False,
        help="Print the title below instead of the standard one (Invoice, Credit Note, ...) on this document only.")
    nc_print_title = fields.Char(
        string='Custom title', copy=False,
        help="e.g. TAX INVOICE or فاتورة ضريبية. The document number is still printed after it.")

    @api.constrains('nc_print_title_enabled', 'nc_print_title')
    def _check_nc_print_title(self):
        for move in self:
            if not move.nc_print_title_enabled:
                continue
            title = (move.nc_print_title or '').strip()
            if not title:
                raise ValidationError(self.env._("Enter the custom print title, or switch the option off."))
            if len(title) > NC_PRINT_TITLE_MAX or '\n' in title or '\r' in title:
                raise ValidationError(self.env._(
                    "The custom print title must be one line of at most %(max)s characters.", max=NC_PRINT_TITLE_MAX))

    @api.onchange('nc_print_title_enabled')
    def _onchange_nc_print_title_enabled(self):
        if not self.nc_print_title_enabled:
            self.nc_print_title = False

    def _nc_document_title(self):
        """The custom title when this document has one switched on, else False (the stock heading prints)."""
        self.ensure_one()
        title = (self.nc_print_title or '').strip()
        return title if self.nc_print_title_enabled and title else False
