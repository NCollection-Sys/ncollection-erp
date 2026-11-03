# -*- coding: utf-8 -*-
"""Purchase's part of the Workspace Administration Center's Business Settings under Option A (see ncollection_core's
business_settings.py). Two purchase settings are not fields Odoo stores directly: its settings page turns "Lock
Confirmed Orders" and "Purchase Order Approval" into the company's ``po_lock`` and ``po_double_validation``
(purchase/models/res_config_settings.py ``set_values``); the same translation is done here."""
from odoo import api, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    @api.model
    def _nc_tenant_admin_company_fields(self):
        return super()._nc_tenant_admin_company_fields() | {
            'po_lock', 'po_double_validation', 'po_double_validation_amount',
        }
