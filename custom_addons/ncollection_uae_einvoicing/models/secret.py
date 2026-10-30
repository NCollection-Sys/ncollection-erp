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
_IDENTITY = {'company_id', 'provider_id', 'secret_name', 'key_version', 'set_by', 'set_at', 'rotated_by',
             'rotated_at'}


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
    # the audit of the LAST key rotation of this credential (who ran it, when); the value itself never changes there
    rotated_by = fields.Many2one('res.users', readonly=True)
    rotated_at = fields.Datetime(readonly=True)

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
            version, ring = secret_crypto.keyring_from_env()
            blob = secret_crypto.encrypt(ring[version], plaintext, version=version, **context)
        except secret_crypto.SecretError as exc:
            raise UserError(str(exc)) from None
        values = {'ciphertext': blob, 'key_version': version, 'set_by': self.env.user.id,
                  'set_at': fields.Datetime.now()}
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
                self._nc_master_for(self.key_version),
                blob,
                db=self.env.cr.dbname,
                company_id=self.company_id.id,
                provider=str(self.provider_id.id),
                name=self.secret_name,
                version=self.key_version,
            )
        except secret_crypto.SecretError as exc:
            raise UserError(str(exc)) from None

    @staticmethod
    def _nc_master_for(version):
        """The master of ``version`` from the deployment's key ring; refused (never guessed) when it is not there."""
        _current, ring = secret_crypto.keyring_from_env()
        if version not in ring:
            raise secret_crypto.SecretError(
                'the master key of version %s is not configured on this deployment' % version
            )
        return ring[version]

    @api.model
    def action_rotate_keys(self):
        """Re-encrypt every credential of the companies this manager may act for with the CURRENT master (R48).

        Each credential is decrypted with the master of ITS version and re-encrypted with the current one inside its
        own savepoint, so the swap is atomic per credential: a credential that cannot be decrypted (its master is not
        configured, or the ciphertext was tampered with or moved) keeps its OLD ciphertext and version untouched and is
        only counted. No plaintext leaves this method; nothing about a value is logged. Returns counts only.

        The ``sudo()`` reads and writes the service-only ciphertext column, after the explicit manager check and
        restricted to the manager's own allowed companies (the record rule's boundary, applied explicitly here)."""
        if not self.env.user.has_group(MANAGER):
            raise AccessError(self.env._("Only an e-invoicing manager can rotate the credential keys."))
        try:
            current, ring = secret_crypto.keyring_from_env()
        except secret_crypto.SecretError as exc:
            raise UserError(str(exc)) from None
        company_ids = self.env.companies.ids
        secrets = self.sudo().search([('company_id', 'in', company_ids), ('key_version', '!=', current)])
        result = {'current_version': current, 'rotated': 0, 'failed': 0, 'already_current': self.sudo().search_count(
            [('company_id', 'in', company_ids), ('key_version', '=', current)])}
        for secret in secrets:
            context = {
                'db': self.env.cr.dbname,
                'company_id': secret.company_id.id,
                'provider': str(secret.provider_id.id),
                'name': secret.secret_name,
            }
            try:
                with self.env.cr.savepoint():
                    if secret.key_version not in ring:
                        raise secret_crypto.SecretError('missing master')
                    plaintext = secret_crypto.decrypt(
                        ring[secret.key_version], secret.ciphertext, version=secret.key_version, **context)
                    blob = secret_crypto.encrypt(ring[current], plaintext, version=current, **context)
                    del plaintext
                    secret.write({'ciphertext': blob, 'key_version': current, 'rotated_by': self.env.uid,
                                  'rotated_at': fields.Datetime.now()})
                result['rotated'] += 1
            except secret_crypto.SecretError:
                result['failed'] += 1
        return result


class EInvoiceProviderSecrets(models.Model):
    _inherit = 'ncollection.einvoice.provider'

    # what a manager may see about the credentials: name, configured, key version, who and when - never a value
    nc_secret_ids = fields.One2many('ncollection.einvoice.secret', 'provider_id', string='Credentials', readonly=True,
                                    groups=MANAGER)

    def action_nc_rotate_keys(self):
        result = self.env['ncollection.einvoice.secret'].action_rotate_keys()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'warning' if result['failed'] else 'success',
                'sticky': bool(result['failed']),
                'title': self.env._('Credential key rotation'),
                'message': self.env._(
                    "Current key version %(current)s: %(rotated)s re-encrypted, %(already)s already current, "
                    "%(failed)s could not be re-encrypted and were left unchanged.",
                    current=result['current_version'], rotated=result['rotated'],
                    already=result['already_current'], failed=result['failed'],
                ),
            },
        }


class EInvoiceSecretWizard(models.TransientModel):
    """Set a provider credential. WRITE-THROUGH: the value is handed to ``set_secret`` inside ``create`` and is never
    stored, not even in this transient table (a wizard row persists until the vacuum)."""

    _name = 'ncollection.einvoice.secret.wizard'
    _description = 'Set a UAE e-invoicing provider credential'

    provider_id = fields.Many2one('ncollection.einvoice.provider', required=True, ondelete='cascade')
    secret_name = fields.Char(required=True, default='api_token')
    value = fields.Char(string='Value', help="Encrypted at once; never shown again, never stored in clear.")

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
