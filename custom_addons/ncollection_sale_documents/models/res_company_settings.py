# -*- coding: utf-8 -*-
"""Sales' part of the Workspace Administration Center's Business Settings under Option A (see ncollection_core's
business_settings.py): the sales settings a workspace Owner changes, applied where Odoo itself stores them."""
from odoo import api, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    @api.model
    def _nc_tenant_admin_company_fields(self):
        return super()._nc_tenant_admin_company_fields() | {
            'quotation_validity_days', 'portal_confirmation_sign', 'portal_confirmation_pay', 'prepayment_percent',
        }
