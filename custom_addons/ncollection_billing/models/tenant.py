# -*- coding: utf-8 -*-
"""Tenant billing contact (P2-T11).

An admin-DB invoice needs a customer partner. Each tenant gets one res.partner
(the billing customer), created lazily on first invoice.
"""
from odoo import fields, models


class Tenant(models.Model):
    _inherit = 'ncollection.tenant'

    partner_id = fields.Many2one(
        'res.partner', string='Billing Contact', copy=False,
        help='The customer record this tenant is invoiced against (admin DB).')

    def _nc_ensure_partner(self):
        """Return the tenant's billing partner, creating it once if needed."""
        self.ensure_one()
        if not self.partner_id:
            self.partner_id = self.env['res.partner'].create({  # arch-guard: admin-db-billing
                'name': self.company_name,
                'email': self.email or False,
                'company_type': 'company',
            })
        return self.partner_id
