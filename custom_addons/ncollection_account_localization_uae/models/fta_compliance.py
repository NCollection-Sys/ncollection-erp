# -*- coding: utf-8 -*-
"""UAE FTA compliance checklist (F5-T01).

A per-company status model tracking how FTA-ready a tenant is. It is bespoke
NCollection business-layer tracking (no Odoo/OCA equivalent exists — oca-scout
survey on #126), NOT an accounting mechanism: Odoo still owns taxes/journals.

The standard rows below are the umbrella the downstream tickets flip to 'done'
as they land — TRN validation here (#126), VAT config (#44), CoA (#45),
FTA-compliant invoices (#49).
"""
from odoo import api, fields, models

# (key, label, sequence) — the standard UAE FTA readiness checklist. `key` is the
# stable technical id used for idempotent seeding; the label is user-facing.
STANDARD_FTA_ITEMS = [
    ('trn_registered', 'TRN registered with the FTA', 10),
    ('tax_period_configured', 'VAT tax period configured', 20),
    ('vat_return_schedule', 'VAT return filing schedule set', 30),
    ('invoice_format_compliant', 'Tax invoices meet FTA format (TRN + VAT breakdown)', 40),
    ('einvoicing_ready', 'E-invoicing readiness (structured data / QR)', 50),
]


class FtaComplianceItem(models.Model):
    _name = 'ncollection.fta.compliance.item'
    _description = 'UAE FTA Compliance Item'
    _order = 'company_id, sequence, id'

    key = fields.Char(
        required=True, index=True,
        help='Stable technical identifier for the requirement (used for '
             'idempotent seeding).')
    name = fields.Char(required=True, translate=True)
    company_id = fields.Many2one(
        'res.company', required=True, ondelete='cascade', index=True,
        default=lambda self: self.env.company)
    state = fields.Selection(
        [('pending', 'Pending'), ('done', 'Done'), ('not_applicable', 'Not applicable')],
        default='pending', required=True)
    note = fields.Text()
    sequence = fields.Integer(default=10)

    # Odoo 19: _sql_constraints is silently ignored — use models.Constraint
    # (one checklist row per (company, requirement)).
    _unique_company_key = models.Constraint(
        'unique(company_id, key)',
        'This FTA compliance item already exists for this company.')

    @api.model
    def _ensure_for_company(self, company):
        """Create any missing standard FTA items for `company`. Idempotent —
        safe to call at install (existing companies) and on company create."""
        existing = set(self.search([('company_id', '=', company.id)]).mapped('key'))
        vals = [
            {'key': key, 'name': name, 'sequence': seq, 'company_id': company.id}
            for key, name, seq in STANDARD_FTA_ITEMS if key not in existing
        ]
        if vals:
            self.create(vals)
