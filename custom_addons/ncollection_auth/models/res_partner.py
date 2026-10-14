# -*- coding: utf-8 -*-
from odoo import api, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    @api.model
    def _get_partner_from_token(self, token):
        """A link that has already been used resolves to no partner, so every consumer (the form, the check, the reset)
        refuses it the same way it refuses an expired or forged one."""
        if token and self.env['ncollection.auth.spent_token']._is_spent(token):
            return None
        return super()._get_partner_from_token(token)
