"""A posted transfer records the incoming method its receiving payment REALLY used (the compute would only guess the
journal's first method). Drafts keep the computed default. Then this release's new terms are loaded."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE ncollection_internal_transfer t
           SET destination_payment_method_line_id = p.payment_method_line_id
          FROM account_payment p
         WHERE p.id = t.destination_payment_id AND t.state = 'posted'
    """)
    env = api.Environment(cr, SUPERUSER_ID, {})
    langs = [code for code, _name in env['res.lang'].get_installed() if code != 'en_US']
    if langs:
        env['ir.module.module'].search([('name', '=', 'ncollection_journal_operations')])._update_translations(
            langs, overwrite=False)
