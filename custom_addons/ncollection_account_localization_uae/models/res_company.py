# -*- coding: utf-8 -*-
"""Per-company FTA readiness rollup + auto-seeding (F5-T01)."""
from odoo import api, fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    fta_item_ids = fields.One2many(
        'ncollection.fta.compliance.item', 'company_id',
        string='FTA Compliance Items')
    fta_readiness = fields.Integer(
        string='FTA Readiness %', compute='_compute_fta_readiness',
        help='Percentage of applicable UAE FTA compliance items marked done '
             '(items marked "not applicable" are excluded from the base).')

    @api.depends('fta_item_ids.state')
    def _compute_fta_readiness(self):
        for company in self:
            applicable = company.fta_item_ids.filtered(
                lambda i: i.state != 'not_applicable')
            done = applicable.filtered(lambda i: i.state == 'done')
            company.fta_readiness = (
                round(100 * len(done) / len(applicable)) if applicable else 0)

    @api.model_create_multi
    def create(self, vals_list):
        companies = super().create(vals_list)
        # Seed each new company's FTA checklist (sudo: company creation may run
        # in a context without account-manager create rights on the item model).
        Item = self.env['ncollection.fta.compliance.item'].sudo()
        for company in companies:
            Item._ensure_for_company(company)
        return companies
