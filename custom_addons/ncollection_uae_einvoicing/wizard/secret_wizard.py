# -*- coding: utf-8 -*-
"""The credential wizard: the only place a provider secret is typed (see models/secret.py for the store)."""

from odoo import api, fields, models
from odoo.exceptions import AccessError

from ..models.secret import MANAGER


class EInvoiceSecretWizard(models.TransientModel):
    """Set a provider credential. WRITE-THROUGH: the value is handed to ``set_secret`` inside ``create`` and is never
    stored, not even in this transient table (a wizard row persists until the vacuum)."""

    _name = 'ncollection.einvoice.secret.wizard'
    _description = 'Set a UAE e-invoicing provider credential'

    provider_id = fields.Many2one('ncollection.einvoice.provider', required=True, ondelete='cascade')
    secret_name = fields.Char(required=True, default='api_token')
    value = fields.Char(help="Encrypted at once; never shown again, never stored in clear.")

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.user.has_group(MANAGER):
            raise AccessError(self.env._("Only an e-invoicing manager can set a provider credential."))
        for vals in vals_list:
            plaintext = vals.pop('value', None)
            provider = self.env['ncollection.einvoice.provider'].browse(vals.get('provider_id'))
            provider.check_access('write')
            if plaintext:
                self.env['ncollection.einvoice.secret'].set_secret(provider, vals.get('secret_name'), plaintext)
            del plaintext
        return super().create(vals_list)

    def action_done(self):
        return {'type': 'ir.actions.act_window_close'}
