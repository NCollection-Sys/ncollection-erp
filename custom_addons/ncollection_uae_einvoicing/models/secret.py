# -*- coding: utf-8 -*-
"""Provider credentials, encrypted at rest, write-only, tenant- and company-scoped.

WHO CAN DO WHAT
* a MANAGER sets a secret through :meth:`set_secret` (a write-only path) and sees only that it is configured, who set
  it and when. Nothing in the UI, in ``read``, ``search_read``, ``export`` or the chatter ever returns it;
* the SERVICE account (non-interactive; submission and status jobs) is the only identity that can decrypt, through
  :meth:`_reveal`, a private method that is not exposed over RPC;
* nobody else, including an administrator who is not in either group, can read the ciphertext column: the field is
  restricted to the service group at the ORM, and the only writer is :meth:`set_secret`.

The ONE ``sudo()`` of this module lives in :meth:`set_secret`, after an explicit manager check, because the ciphertext
column is deliberately not writable by managers directly. Decryption needs the master key from the container
environment (never the database) and the tenant database name + company in the key derivation and the AAD, so a copy of
the table is
useless elsewhere. The secret never enters XML, logs, history, audit rows or exception messages."""

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..lib import secret_crypto

MANAGER = 'ncollection_uae_einvoicing.group_einvoice_manager'
SERVICE = 'ncollection_uae_einvoicing.group_einvoice_service'
# what binds a ciphertext to its place (the AAD of the encryption): changing any of it makes the secret undecryptable
_IDENTITY = {'company_id', 'provider_id', 'secret_name', 'key_version', 'set_by', 'set_at'}


class EInvoiceSecret(models.Model):
    _name = 'ncollection.einvoice.secret'
    _description = 'UAE e-invoicing provider credential (encrypted, write-only)'
    _rec_name = 'secret_name'

    company_id = fields.Many2one('res.company', required=True, index=True, ondelete='cascade')
    provider_id = fields.Many2one('ncollection.einvoice.provider', required=True, ondelete='cascade')
    secret_name = fields.Char(required=True, help="A label chosen by the provider adapter, e.g. 'api_token'.")
    ciphertext = fields.Char(groups='ncollection_uae_einvoicing.group_einvoice_service', copy=False, prefetch=False)
    key_version = fields.Integer(default=1, readonly=True)
    configured = fields.Boolean(compute='_compute_configured')
    set_by = fields.Many2one('res.users', readonly=True)
    set_at = fields.Datetime(readonly=True)

    _name_unique = models.Constraint(
        'unique(company_id, provider_id, secret_name)', 'This secret is already defined for the provider.'
    )

    @api.depends('ciphertext')
    def _compute_configured(self):
        # reading the column's EXISTENCE without exposing it: a raw SQL presence check, never the value
        if not self.ids:
            self.configured = False
            return
        self.flush_model(['ciphertext'])
        self.env.cr.execute(
            "SELECT id FROM ncollection_einvoice_secret "
            "WHERE id IN %s AND ciphertext IS NOT NULL AND ciphertext <> ''",
            (tuple(self.ids),),
        )
        present = {row[0] for row in self.env.cr.fetchall()}
        for secret in self:
            secret.configured = secret.id in present

    @api.constrains('company_id', 'provider_id')
    def _check_company_is_the_providers(self):
        for secret in self:
            if secret.provider_id.company_id != secret.company_id:
                raise ValidationError(self.env._("The credential company must be the company of its provider."))

    def write(self, vals):
        if not self.env.su and _IDENTITY & set(vals):
            raise UserError(self.env._("A credential's identity cannot be changed; set it again instead."))
        return super().write(vals)

    @api.model
    def set_secret(self, provider, secret_name, plaintext):
        """Create or replace a secret (write-only). Returns the record WITHOUT its value."""
        if not self.env.user.has_group(MANAGER):
            raise AccessError(self.env._("Only an e-invoicing manager can set a provider credential."))
        if not plaintext or not str(plaintext).strip():
            raise UserError(self.env._("A credential cannot be empty."))
        record = self.search([('provider_id', '=', provider.id), ('secret_name', '=', secret_name)], limit=1)
        context = {
            'db': self.env.cr.dbname,
            'company_id': provider.company_id.id,
            'provider': str(provider.id),
            'name': secret_name,
        }
        try:
            blob = secret_crypto.encrypt(secret_crypto.master_from_env(), plaintext, **context)
        except secret_crypto.SecretError as exc:
            raise UserError(str(exc)) from None
        values = {'ciphertext': blob, 'key_version': 1, 'set_by': self.env.user.id, 'set_at': fields.Datetime.now()}
        if record:
            record.sudo().write(values)  # the single sudo: ciphertext is service-only at the ORM
            return record
        return self.sudo().create(
            dict(values, company_id=provider.company_id.id, provider_id=provider.id, secret_name=secret_name)
        )

    def _reveal(self):
        """The plaintext. Service identity only; a private method, never reachable through RPC."""
        self.ensure_one()
        if not self.env.user.has_group(SERVICE):
            raise AccessError(self.env._("Only the e-invoicing service account can use a provider credential."))
        blob = self.ciphertext
        if not blob:
            raise UserError(self.env._("The credential is not set."))
        try:
            return secret_crypto.decrypt(
                secret_crypto.master_from_env(),
                blob,
                db=self.env.cr.dbname,
                company_id=self.company_id.id,
                provider=str(self.provider_id.id),
                name=self.secret_name,
                version=self.key_version,
            )
        except secret_crypto.SecretError as exc:
            raise UserError(str(exc)) from None
