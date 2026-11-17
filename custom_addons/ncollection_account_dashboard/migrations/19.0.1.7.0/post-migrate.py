"""Load this release's NEW terms in every active language (Supplier wording, Settings page, XBRL, footer zones).

A targeted `-u` does not import terms a language did not have yet. Overwrite is off: a translation someone changed is
kept; the Interface Text engine re-applies label overrides at the next registry load."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    langs = [code for code, _name in env['res.lang'].get_installed() if code != 'en_US']
    if langs:
        env['ir.module.module'].search([('name', '=', 'ncollection_account_dashboard')])._update_translations(langs, overwrite=False)
