# -*- coding: utf-8 -*-
"""Tenant Administration: a Platform Administrator manages a tenant's users and the Tenant Administrator's password.

Database-per-tenant and Rule 3 mean the platform never reads or writes tenant tables. Every operation is one call over
the same per-tenant, derived-bearer JSON-2 channel Enter Company and config-sync use, into the tenant's
``ncollection.admin.service`` (ncollection_core), which does the work INSIDE the tenant database through the same
guards the tenant's own Workspace Administration uses (last Administrator, seat limit, forbidden groups).

What decides whether you may act, ALL on the platform and ALL server-side, checked again on every call (a wizard that
was open a while is not an authorisation):
  * you are a platform administrator (group), AND
  * you are on THIS tenant's ``platform_admin_ids`` allow-list (the list that already governs Enter Company), AND
  * the tenant database is provisioned.
The database is ``tenant.database_name`` from the registry, never a value from the caller.

Passwords: the platform stores none. A temporary password is generated INSIDE the tenant and shown once to the
administrator in the confirmation; a typed password travels in the request body to the tenant and is cleared from the
dialog before the call. Neither is logged here, written to the chatter or kept in any record; the chatter and the
tenant's audit log say THAT a reset happened and who did it.
"""
import json
import logging
import os
import re

import requests

from odoo import models
from odoo.exceptions import AccessError, UserError

from .config_sync import _RPC_TIMEOUT, _SYNC_KEY_ENV, derive_tenant_key

_logger = logging.getLogger(__name__)

_ADMIN_ENDPOINT = '/json/2/ncollection.admin.service/%s'
_PLATFORM_GROUP = 'ncollection_subscription.group_platform_admin'
_DB_RE = re.compile(r'^[a-z][a-z0-9]{1,62}$')
_MAX_REPLY = 512 * 1024
# The only tenant exceptions whose message is shown to the operator: written for people, never a traceback.
_SHOWN_ERRORS = ('odoo.exceptions.UserError', 'odoo.exceptions.ValidationError', 'odoo.exceptions.AccessError')


class Tenant(models.Model):
    _inherit = 'ncollection.tenant'

    # ------------------------------------------------------------------ authorisation
    def _nc_admin_authorise(self):
        """Raise unless the CURRENT platform user may administer this tenant's users. Returns the database name."""
        self.ensure_one()
        user = self.env.user
        if not user.has_group(_PLATFORM_GROUP):
            raise AccessError(self.env._("Only a Platform Administrator can manage a company's users."))
        if user not in self.platform_admin_ids:
            raise AccessError(self.env._("You are not authorised to manage the users of %s.", self.company_name))
        db = (self.database_name or '').strip()
        if self.database_status != 'ready' or not _DB_RE.fullmatch(db):
            raise UserError(self.env._("%s has no workspace to manage yet.", self.company_name))
        return db

    # ------------------------------------------------------------------ the one door to the tenant
    def _nc_admin_call(self, method, payload=None):
        """Call ``ncollection.admin.service.<method>`` on this tenant; return the decoded reply (a dict).

        The payload may carry a password; it is never logged and never put in an error message."""
        db = self._nc_admin_authorise()
        master = os.environ.get(_SYNC_KEY_ENV)
        if not master:
            raise UserError(self.env._("The platform is not configured to reach tenants (service key missing)."))
        user = self.env.user
        body = dict(payload or {}, actor={'ref': 'u%d' % user.id, 'name': user.name})
        try:
            resp = requests.post(
                self._config_sync_base_url() + _ADMIN_ENDPOINT % method,
                headers={
                    'Host': self._nc_service_host(db),
                    'Authorization': 'Bearer %s' % derive_tenant_key(master, db),
                    'X-Odoo-Database': db, 'Content-Type': 'application/json', 'Accept-Encoding': 'identity',
                },
                data=json.dumps({'vals': body}), timeout=_RPC_TIMEOUT, allow_redirects=False, stream=True)
        except requests.RequestException as exc:
            _logger.error("Tenant administration (%s) to %s failed (transport): %s", method, db, type(exc).__name__)
            raise UserError(self.env._("%s could not be reached. Try again in a moment.", self.company_name))
        try:
            raw = resp.raw.read(_MAX_REPLY + 1, decode_content=True)
            status = resp.status_code
        finally:
            resp.close()
        if len(raw) > _MAX_REPLY:
            raise UserError(self.env._("%s sent a reply that is too large.", self.company_name))
        try:
            data = json.loads(raw or b'{}')
        except ValueError:
            data = {}
        if status == 200:
            return data
        _logger.warning("Tenant administration (%s) to %s refused: HTTP %s", method, db, status)
        if status in (404, 405):
            raise UserError(self.env._(
                "%s does not support this yet: its workspace modules are out of date. Upgrade ncollection_core on it "
                "(Fleet Migration), then try again.", self.company_name))
        if status in (401, 403):
            raise UserError(self.env._(
                "%s did not accept the request (the service channel is not authorised).", self.company_name))
        if isinstance(data, dict) and data.get('name') in _SHOWN_ERRORS and data.get('message'):
            raise UserError(str(data['message'])[:400])
        raise UserError(self.env._(
            "%(company)s could not complete the request (HTTP %(status)s).",
            company=self.company_name, status=status))

    def _nc_admin_note(self, text):
        """What the platform records about an administrative action: who and what, never a secret."""
        self.sudo().message_post(
            body=text, author_id=self.env.user.partner_id.id, subtype_xmlid='mail.mt_note')

    # ------------------------------------------------------------------ the screens
    def action_open_users(self):
        """Open the tenant's user list (read from the tenant database now)."""
        self.ensure_one()
        reply = self._nc_admin_call('list_users')
        Users = self.env['ncollection.tenant.users']
        users = Users.create({
            'tenant_id': self.id, 'max_users': reply.get('max_users') or 0,
            'active_internal': reply.get('active_internal') or 0,
            'line_ids': [(0, 0, {
                'remote_id': row['id'], 'name': row['name'], 'login': row['login'], 'email': row.get('email') or '',
                'active': bool(row.get('active')), 'administrator': bool(row.get('administrator')),
                'is_tenant_admin': row['login'] == self._nc_admin_login(),
                'role_names': ', '.join(row.get('roles') or []),
                'must_change_password': bool(row.get('must_change_password')),
                'last_login': row.get('last_login') or '',
            }) for row in reply.get('users', [])],
        })
        return {
            'type': 'ir.actions.act_window', 'name': self.env._('Users of %s', self.company_name),
            'res_model': Users._name, 'res_id': users.id, 'view_mode': 'form', 'target': 'current',
        }

    def action_reset_admin_password(self):
        """Reset the Tenant Administrator's password: finds that account by the login recorded at provisioning."""
        self.ensure_one()
        login = self._nc_admin_login()
        if not login:
            raise UserError(self.env._("This tenant has no administrator account recorded."))
        reply = self._nc_admin_call('list_users')
        row = next((r for r in reply.get('users', []) if r['login'] == login), None)
        if not row:
            raise UserError(self.env._("The administrator account %s was not found in the workspace.", login))
        return self.env['ncollection.tenant.user.password']._nc_open(self, row['id'], row['name'])
