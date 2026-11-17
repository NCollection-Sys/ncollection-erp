# -*- coding: utf-8 -*-
"""Inventory's company fields a Workspace Administrator (or a delegate of Inventory Settings) may change through the
unified Settings (ncollection_core res_company_delegation.py; section from settings_policy COMPANY_FIELD_SECTION)."""
from odoo import api, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    @api.model
    def _nc_tenant_admin_company_fields(self):
        return super()._nc_tenant_admin_company_fields() | {'annual_inventory_month', 'annual_inventory_day'}
