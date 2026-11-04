# -*- coding: utf-8 -*-
"""Duplicate on products and payment terms (DUP-01): the model's own ``copy()``, wrapped by nc.duplicate.mixin.

A product's internal reference and barcode identify ONE product in a scan or a price list: the copy starts without
them (the barcode is already ``copy=False``). Taxes, journals and posted accounting documents are deliberately NOT
offered a duplicate here: a tax or an account is configuration the accountant creates on purpose."""
from odoo import models


class ProductTemplate(models.Model):
    _inherit = ['product.template', 'nc.duplicate.mixin']

    def _nc_duplicate_defaults(self):
        defaults = super()._nc_duplicate_defaults()
        defaults.update({'default_code': False, 'barcode': False})
        return defaults


class AccountPaymentTerm(models.Model):
    _inherit = ['account.payment.term', 'nc.duplicate.mixin']
