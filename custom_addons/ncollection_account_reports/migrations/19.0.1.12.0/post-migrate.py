# -*- coding: utf-8 -*-
"""The Financial Report document type gains its Arabic name (it had none).

Reload this module's own translations with OVERWRITE.

Odoo loads a module's .po on upgrade WITHOUT overwriting a translation that already exists. A label whose English
changed (NCollection SaaS -> Business Management, New User -> Add User...) keeps its OLD stored translation on every
database that had it, so the Arabic interface went on showing the old name. Only this module's terms are reloaded,
through Odoo's own loader; nothing else is written. Idempotent.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    langs = [code for code, _name in env['res.lang'].get_installed() if code != 'en_US']
    if langs:
        env['ir.module.module']._load_module_terms(['ncollection_account_reports'], langs, overwrite=True)
