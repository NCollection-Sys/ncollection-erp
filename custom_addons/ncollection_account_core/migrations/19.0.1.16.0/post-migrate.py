"""Apply the platform's capabilities once the capability group exists (it is created by this release), and load this
release's new terms. Idempotent: with no capability pushed nothing is granted."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['ncollection.settings.policy']._nc_apply_capabilities()
    langs = [code for code, _name in env['res.lang'].get_installed() if code != 'en_US']
    if langs:
        env['ir.module.module'].search([('name', '=', 'ncollection_account_core')])._update_translations(
            langs, overwrite=False)
