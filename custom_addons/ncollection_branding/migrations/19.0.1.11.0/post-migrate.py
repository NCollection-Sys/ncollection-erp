# -*- coding: utf-8 -*-
"""Product Branding becomes System Branding: new columns (system colours, copyright, browser title) on the SAME table.

Nothing is renamed or moved: `ncollection_product_branding`, its row and its images stay exactly as they are, so a
workspace's configured system name (e.g. its own product name) survives untouched. The new columns are added by the
ORM's schema update; this only makes sure an existing row has valid system colours. Idempotent (a colour somebody chose
is never changed) and free of hand-written schema SQL.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    api.Environment(cr, SUPERUSER_ID, {})['ncollection.product.branding']._nc_adopt_system_defaults()
