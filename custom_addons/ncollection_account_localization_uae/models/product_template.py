# -*- coding: utf-8 -*-
"""UAE e-invoicing item classification, kept CONDITIONAL.

PINT-AE (1.0.4) asks for an item type (``BTAE-13``: Goods / Services / Both) and, depending on it, a goods
classification (``BT-158``, scheme ``HS``) and/or a service accounting code (``BTAE-17``, scheme ``SAC``):
``ibr-184-ae`` Goods ⇒ HS, ``ibr-185-ae`` Services ⇒ SAC, ``ibr-186-ae`` Both ⇒ HS + SAC.

None of the three is required on a product. A product only has to satisfy the rule matching the type it declares, at
the moment it is put on a customer e-invoice, and the e-invoice readiness report says which products fall short.
Forcing them on every product would burden items that are never sold to a UAE e-invoicing customer. The minimum
digit count (``ibr-187-ae``) is a placeholder in the official Schematron (``test="true()"``) so no length is
enforced here.
"""

from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    nc_item_type = fields.Selection(
        [('G', 'Goods'), ('S', 'Services'), ('B', 'Both')],
        string='E-invoice item type',
        copy=True,
        help="PINT-AE item type. Goods need an HS code, services a SAC code, 'Both' needs both, when the product is "
        "invoiced on a UAE e-invoice. Leave empty if the product is never e-invoiced.",
    )
    nc_hs_code = fields.Char(
        string='HS code', copy=True, help="Harmonized System goods classification (PINT-AE scheme 'HS')."
    )
    nc_sac_code = fields.Char(string='SAC code', copy=True, help="Service accounting code (PINT-AE scheme 'SAC').")
