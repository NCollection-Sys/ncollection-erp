"""Footer zones: a company that never set a zone keeps its legacy Footer content, now in the Center zone (every
language of it), once. The legacy field is left untouched (a layout prints the zones INSTEAD of it, never both), so
going back is only clearing the zones. Then this release's new terms are loaded."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE res_company
           SET nc_footer_center = report_footer
         WHERE report_footer IS NOT NULL
           AND nc_footer_left IS NULL AND nc_footer_center IS NULL AND nc_footer_right IS NULL
    """)
    env = api.Environment(cr, SUPERUSER_ID, {})
    langs = [code for code, _name in env['res.lang'].get_installed() if code != 'en_US']
    if langs:
        env['ir.module.module'].search([('name', '=', 'ncollection_branding')])._update_translations(
            langs, overwrite=False)
