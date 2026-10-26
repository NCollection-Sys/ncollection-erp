# -*- coding: utf-8 -*-
"""Reload this module's translations with OVERWRITE (R-040): "Print Settings" became "Print Templates", and Odoo's
upgrade keeps an existing stored translation, so the Arabic name would stay the old one. The Print Template fields are
new columns with defaults and need no data migration. Idempotent."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    langs = [code for code, _name in env['res.lang'].get_installed() if code != 'en_US']
    if langs:
        env['ir.module.module']._load_module_terms(['ncollection_branding'], langs, overwrite=True)
