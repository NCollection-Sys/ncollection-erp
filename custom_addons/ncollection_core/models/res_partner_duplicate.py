# -*- coding: utf-8 -*-
"""Duplicate on Contacts. The copy is Odoo's own ``res.partner.copy()``. What a duplicate must NOT carry
is a legal identity: the UAE identifiers (TRN, TIN, registration) are already ``copy=False`` in
ncollection_account_localization_uae; Odoo's own VAT / Tax ID and company registry are copied by default
and are cleared here, so a duplicated customer is never a second holder of the same tax number."""
from odoo import models


class ResPartner(models.Model):
    _inherit = ['res.partner', 'nc.duplicate.mixin']

    def _nc_duplicate_defaults(self):
        defaults = super()._nc_duplicate_defaults()
        defaults.update({'vat': False, 'company_registry': False})
        return defaults
