# -*- coding: utf-8 -*-
"""Request 09.2: ONE place that decides the colours of money movements in every financial table (bank transactions,
payments, statements, ledgers, customers / suppliers): Accounting Settings > Financial Colours, per company.

The web client receives the palette in its session and sets ``data-nc-fin-palette`` on the document root; the
stylesheet (static/src/fin_palette/fin_palette.scss) maps it to two CSS custom properties, ``--nc-fin-in`` (additions)
and ``--nc-fin-out`` (deductions), that the tables use through the classes ``o_nc_fin_in`` / ``o_nc_fin_out``. No
table hard-codes a colour. Direction is never told by colour alone: columns are named (Incoming / Outgoing, Debit /
Credit) and negative balances carry their minus sign. Status badges keep their own colours (state, not direction).
"""
from odoo import fields, models
from odoo.http import request

PALETTES = [
    ('blue_red', 'Blue additions / red deductions (default)'),
    ('green_red', 'Green additions / red deductions'),
    ('blue_orange', 'Blue additions / orange deductions (colour-blind friendly)'),
    ('neutral', 'Neutral (no colour, signs and labels only)'),
]


class ResCompany(models.Model):
    _inherit = 'res.company'

    nc_fin_palette = fields.Selection(PALETTES, string='Financial Colours', default='blue_red', required=True)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    nc_fin_palette = fields.Selection(related='company_id.nc_fin_palette', readonly=False, required=True)


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    def session_info(self):
        info = super().session_info()
        if request and request.session.uid:
            info['nc_fin_palette'] = self.env.company.sudo().nc_fin_palette or 'blue_red'
        return info
