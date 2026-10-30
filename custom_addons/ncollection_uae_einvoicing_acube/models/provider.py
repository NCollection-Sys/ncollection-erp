# -*- coding: utf-8 -*-
"""A-Cube on the provider record: the adapter choice and the environment. Nothing else: credentials are the engine's
encrypted, write-only secrets (``email``, ``password``, ``webhook_token``), and the hosts are constants in
lib/acube.py,
never configuration (so a provider record cannot point the adapter at another server)."""

from odoo import fields, models


class EInvoiceProviderAcube(models.Model):
    _inherit = 'ncollection.einvoice.provider'

    adapter = fields.Selection(selection_add=[('acube', 'A-Cube (PINT AE)')], ondelete={'acube': 'set null'})
    nc_acube_environment = fields.Selection(
        [('sandbox', 'Sandbox'), ('production', 'Production')], string='A-Cube environment', default='sandbox',
        required=True,
        help="Sandbox: ae-sandbox.api.acubeapi.com (test network). Production: ae.api.acubeapi.com. Credentials are "
        "issued per environment by A-Cube.")
