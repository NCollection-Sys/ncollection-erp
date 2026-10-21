# -*- coding: utf-8 -*-
"""The kinds of printed document a stamp can be scoped to.

A document type is a NAME (Customer Invoice, Payment Receipt...) tied to the report(s) that print it and, when a report
prints several kinds, a record domain that tells them apart (one invoice report prints invoices, bills and credit
notes; the domain on ``move_type`` says which is which). The owning module declares its own types as data
(accounting: invoices / bills / credit notes / receipts; sales: quotation / order; purchase: RFQ / order; reports:
the financial reports), so a workspace without Purchase does not offer a Purchase stamp, and a stamp can only ever be
scoped to a document that exists. Nothing here prints anything.
"""
import ast

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class NcDocumentType(models.Model):
    _name = 'ncollection.document.type'
    _description = 'Printed document type'
    _order = 'sequence, id'

    name = fields.Char(string='Document', required=True, translate=True)
    code = fields.Char(required=True, index=True, help="Stable identifier used by data and code; never shown.")
    sequence = fields.Integer(default=10)
    report_ids = fields.Many2many(
        'ir.actions.report', 'ncollection_document_type_report_rel', 'type_id', 'report_id', string='Printed by',
        help="The report(s) whose PDF is this kind of document.")
    manager_group_ids = fields.Many2many(
        'res.groups', 'ncollection_document_type_group_rel', 'type_id', 'group_id', string='Who may stamp it',
        help="Besides Settings administrators (who may stamp every kind), the groups allowed to create or change a "
             "stamp for this kind of document. Enforced by the model on every write path, not only in the form. "
             "A kind with no group here can be stamped by Settings administrators only.")
    record_domain = fields.Char(
        string='Only when', default='[]',
        help="A domain on the printed record that tells this kind of document apart from the other kinds the same "
             "report prints, for example the move type. Empty (the default) means every record of the report.")

    _code_unique = models.Constraint('unique(code)', 'A document type code must be unique.')

    @api.constrains('record_domain')
    def _check_record_domain(self):
        for doc_type in self:
            try:
                domain = ast.literal_eval(doc_type.record_domain or '[]')
            except (ValueError, SyntaxError):
                domain = None
            if not isinstance(domain, list):
                raise ValidationError(self.env._(
                    "The condition of %(name)s must be a plain domain list such as [('state', '=', 'sale')].",
                    name=doc_type.name))

    def _nc_matches(self, record):
        """True when ``record`` is a document of this kind (its domain holds; no domain means every record)."""
        self.ensure_one()
        domain = ast.literal_eval(self.record_domain or '[]')
        return not domain or bool(record.filtered_domain(domain))

    @api.model
    def _nc_types_for(self, report, record):
        """The kinds of document ``record`` is when printed by ``report`` (usually exactly one)."""
        return self.search([('report_ids', 'in', report.ids)]).filtered(lambda doc_type: doc_type._nc_matches(record))
