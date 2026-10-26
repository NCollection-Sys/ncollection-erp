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
from odoo.exceptions import AccessError, ValidationError


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

    # ------------------------------------------------------------------ Print Settings
    # A document type is also where its PRINT SETTINGS live: one controlled record per kind of document, shipped as
    # data by the module that prints it and surviving every upgrade. Only these fields can be changed, and only by
    # who may manage that kind (the same rule as its stamps); the rest of the record is data.
    paperformat_id = fields.Many2one(
        'report.paperformat', string='Paper format',
        help="The paper, orientation and margins this document prints on, in Preview and Print alike. Documents "
             "printed by the same report share it. Empty: the document paper sized for the letterhead, otherwise the "
             "company's "
             "paper format. A report that declares its own paper format (a cheque) keeps it.")

    @api.model
    def _nc_print_setting_fields(self):
        """The fields a manager of a kind may change. Modules that add an optional section add its switch here."""
        return {'paperformat_id'}

    def _nc_check_print_settings_allowed(self, vals):
        if self.env.su:
            return
        if set(vals) - self._nc_print_setting_fields():
            raise AccessError(self.env._("Only the print settings of a document type can be changed."))
        user = self.env.user
        if user.has_group('base.group_system'):
            return
        mine = user.all_group_ids
        refused = self.filtered(lambda kind: not (kind.manager_group_ids & mine))
        if refused:
            raise AccessError(self.env._(
                "You are not allowed to change the print settings of: %(kinds)s.",
                kinds=', '.join(refused.mapped('name'))))

    def write(self, vals):
        targets = self
        if 'paperformat_id' in vals and not self.env.context.get('install_mode'):
            # the paper is a property of the REPORT (wkhtmltopdf prints one batch on one paper): kinds printed by the
            # same report (invoice, bill, credit note) share it, so choosing it for one chooses it for all of them
            targets |= self.search([('report_ids', 'in', self.report_ids.ids)])
        targets._nc_check_print_settings_allowed(vals)
        return super(NcDocumentType, targets).write(vals)

    @api.model
    def _nc_paperformat_for(self, report):
        """The paper ``report`` prints on by its document type's Print Settings, or an empty recordset."""
        kinds = self.sudo().search([('report_ids', 'in', report.ids), ('paperformat_id', '!=', False)], limit=1)
        return kinds.paperformat_id
