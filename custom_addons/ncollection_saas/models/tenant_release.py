# -*- coding: utf-8 -*-
"""The platform's view of each tenant's release compatibility: the control-plane half of the release gate.

The tenant side (``ncollection_core/release_gate.py``) decides, before any registry is loaded, whether a database may
receive traffic from the code that serves it, and answers its state to the platform's bearer on
``/ncollection/release/status``. This model is where the platform KEEPS that answer, for the operator (the tenant form
shows the state and the exact reason), for Company Entry (no token is issued into a tenant that is not READY), for
provisioning (a tenant is handed over only once it validates) and for Fleet Migration (a tenant is held while it is
upgraded, opened only after it validates, and kept held when it fails).

Rule 3: the platform never reads a tenant database. Everything here goes over HTTP to the tenant, with the tenant's
own derived config-sync bearer, through the same internal address and Host header config sync and Company Entry use.
"""
import json
import logging
import os
import re
from datetime import datetime

import requests

from odoo import fields, models
from odoo.exceptions import AccessError, UserError

from .config_sync import _RPC_TIMEOUT, _SYNC_KEY_ENV, derive_tenant_key

_logger = logging.getLogger(__name__)

STATUS_PATH = '/ncollection/release/status'
HOLD_PATH = '/ncollection/release/hold'
OPEN_PATH = '/ncollection/release/open'
_MAX_REPLY = 256 * 1024
_PLATFORM_GROUP = 'ncollection_subscription.group_platform_admin'
_STAMP = re.compile(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z\s*(.*)$', re.S)   # the gate's "<utc iso>Z text"

RELEASE_STATES = [
    ('unknown', 'Not checked'),
    ('ready', 'Ready'),
    ('upgrade_required', 'Upgrade required'),
    ('upgrading', 'Upgrading'),
    ('upgrade_failed', 'Upgrade failed'),
    ('blocked', 'Blocked'),
    ('provisioning', 'Provisioning'),
    ('unreachable', 'Unreachable'),
]
_KNOWN = {value for value, _label in RELEASE_STATES}


class TenantRelease(models.Model):
    _inherit = 'ncollection.tenant'

    nc_release_state = fields.Selection(
        RELEASE_STATES, string='Release state', default='unknown', readonly=True, copy=False, index=True,
        help="Whether this tenant's database may receive traffic from the code the platform runs, as its release gate "
             "last answered. Only READY tenants can be entered.")
    nc_release_detail = fields.Text(
        string='Release detail', readonly=True, copy=False,
        help="Why the tenant is not READY: modules behind the code (database version < code version), modules "
             "installed but not deployed, a hold and its reason.")
    nc_release_checked_at = fields.Datetime(string='Release checked at', readonly=True, copy=False)
    nc_release_fingerprint = fields.Char(
        string='Database fingerprint', readonly=True, copy=False,
        help="Fingerprint of the module versions this tenant's database is recorded at (release gate).")
    nc_release_code_fingerprint = fields.Char(
        string='Code release fingerprint', readonly=True, copy=False,
        help="Fingerprint of the module versions of the code serving this tenant. When the tenant is READY, its "
             "database is at this release.")
    nc_release_required = fields.Char(
        string='Modules to upgrade', readonly=True, copy=False,
        help="The exact list to upgrade together, in one run, through Fleet Migration (never -u all).")
    nc_release_action = fields.Text(string='Required action', readonly=True, copy=False)
    nc_release_last_ok_at = fields.Datetime(string='Last successful upgrade', readonly=True, copy=False)
    nc_release_last_error = fields.Text(string='Last failed upgrade', readonly=True, copy=False)

    # ------------------------------------------------------------------ transport
    def _nc_release_call(self, path, payload=None):
        """(HTTP status or None, JSON body) of one release call into this tenant. Never raises for a transport error:
        an unreachable tenant is a state (``unreachable``), not an exception."""
        self.ensure_one()
        db = (self.database_name or '').strip()
        master = os.environ.get(_SYNC_KEY_ENV)
        if not db or not master:
            return None, {'error': 'the platform has no service key or the tenant has no database'}
        headers = {
            'Host': self._nc_service_host(db), 'Authorization': 'Bearer %s' % derive_tenant_key(master, db),
            'X-Odoo-Database': db, 'Accept': 'application/json', 'Accept-Encoding': 'identity',
        }
        url = self._config_sync_base_url().rstrip('/') + path
        try:
            if payload is None:
                resp = requests.get(url, headers=headers, timeout=_RPC_TIMEOUT, allow_redirects=False, stream=True)
            else:
                headers['Content-Type'] = 'application/json'
                resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=_RPC_TIMEOUT,
                                     allow_redirects=False, stream=True)
            raw = resp.raw.read(_MAX_REPLY + 1, decode_content=True)   # never buffer more than the cap
            resp.close()
        except requests.RequestException as exc:
            _logger.warning("Release call %s to %s failed (transport): %s", path, db, exc)
            return None, {'error': str(exc)}
        try:
            body = json.loads(raw) if len(raw or b'') <= _MAX_REPLY else {}
        except ValueError:
            body = {}
        return resp.status_code, body if isinstance(body, dict) else {}

    def _nc_release_page(self, path):
        """HTTP status of a real page of this tenant, fetched as the platform (its bearer passes a provisioning or
        upgrade hold while the database is compatible, and nothing else). None when the tenant is unreachable."""
        self.ensure_one()
        db = (self.database_name or '').strip()
        master = os.environ.get(_SYNC_KEY_ENV)
        if not db or not master:
            return None
        try:
            resp = requests.get(
                self._config_sync_base_url().rstrip('/') + path, timeout=_RPC_TIMEOUT, allow_redirects=False,
                headers={'Host': self._nc_service_host(db), 'X-Odoo-Database': db,
                         'Authorization': 'Bearer %s' % derive_tenant_key(master, db)})
        except requests.RequestException as exc:
            _logger.warning("Release page check %s on %s failed (transport): %s", path, db, exc)
            return None
        return resp.status_code

    # ------------------------------------------------------------------ the recorded state
    @staticmethod
    def _nc_release_describe(body):
        lines = ['%s: database %s < code %s' % tuple(row) for row in body.get('behind') or []]
        lines += ['%s: auto-installed by the code, not installed here' % name for name in body.get('absent') or []]
        lines += ['%s: installed but not deployed' % name for name in body.get('missing') or []]
        lines += ['%s: install / upgrade / removal in progress' % name for name in body.get('transitional') or []]
        if body.get('schema_gap'):
            lines.append('schema: a table or column the code declares is missing')
        if body.get('hold'):
            reason = ' (%s)' % body['hold_reason'] if body.get('hold_reason') else ''
            lines.append('hold: %s%s' % (body['hold'], reason))
        return '\n'.join(lines)

    @staticmethod
    def _nc_release_stamp(value):
        """``(datetime or None, text)`` of the gate's ``"<utc iso>Z <text>"`` parameters (last_ok / last_error)."""
        match = _STAMP.fullmatch((value or '').strip())
        if not match:
            return None, (value or '').strip()
        return datetime.strptime(match.group(1), '%Y-%m-%dT%H:%M:%S'), match.group(2).strip()

    def _nc_release_action_text(self, state, body):
        """What the operator has to do, in their language. Derived from the gate's answer only."""
        required = ', '.join(body.get('required') or [])
        absent = ', '.join(body.get('absent') or [])
        if state == 'ready':
            return ''
        if state == 'upgrade_required' and absent:
            return self.env._(
                "Run Fleet Migration for this tenant: it upgrades exactly %(required)s and installs %(absent)s, the "
                "modules the current code installs automatically and this older tenant does not have yet (never all "
                "modules). The tenant opens again only after the run has been validated.",
                required=required or '-', absent=absent)
        if state == 'upgrade_required':
            return self.env._(
                "Upgrade exactly these modules together, in one run, through Fleet Migration (never all modules): "
                "%s. The tenant opens again only after the upgrade has been validated.", required or '-')
        if state == 'upgrading':
            return self.env._(
                "An upgrade is running or is being validated. Wait for Fleet Migration to validate and open the "
                "tenant; do not enter it until it is Ready.")
        if state == 'upgrade_failed':
            return self.env._(
                "The last upgrade failed and the tenant is held. Read the last failed upgrade, correct the cause, "
                "then run the targeted upgrade again through Fleet Migration.")
        if state == 'blocked':
            if body.get('missing'):
                return self.env._(
                    "Modules installed in this tenant are not deployed with the current code: %s. Deploy them, "
                    "or remove them properly, before the tenant can open.", ', '.join(body['missing']))
            if body.get('schema_gap'):
                return self.env._(
                    "The database lacks a table or column the code declares. Run the targeted upgrade of the "
                    "affected modules through Fleet Migration; never repair the schema by hand.")
            return self.env._("An operator held this tenant. Lift the hold once the reason is resolved.")
        if state == 'provisioning':
            return self.env._("The tenant is being provisioned and validated. It opens when validation passes.")
        if state == 'unreachable':
            return self.env._("The platform could not reach the tenant. Check its server, then check again.")
        return self.env._(
            "The tenant did not answer the release status: its code predates the release gate or its service key "
            "does not match. Check its deployment.")

    def _nc_release_record(self, code, body):
        """Write what the tenant answered and return its state. Read from the tenant's release gate over HTTP,
        answered BEFORE the tenant's registry loads, so it is available for exactly the tenants that cannot run."""
        self.ensure_one()
        if code in (200, 409) and body.get('state') in _KNOWN:      # 409: /open refused, with the full state
            state = body['state']
            vals = {'nc_release_state': state, 'nc_release_detail': self._nc_release_describe(body),
                    'nc_release_fingerprint': body.get('db_fingerprint') or False,
                    'nc_release_code_fingerprint': body.get('release_fingerprint') or False,
                    'nc_release_required': ','.join((body.get('required') or []) + (body.get('absent') or []))
                                           or False}
            ok_at, _ok = self._nc_release_stamp(body.get('last_ok'))
            error_at, error = self._nc_release_stamp(body.get('last_error'))
            if ok_at:
                vals['nc_release_last_ok_at'] = ok_at
            if error and (not ok_at or not error_at or error_at > ok_at):
                vals['nc_release_last_error'] = '%s %s' % (body['last_error'].split()[0], error) if error_at else error
            elif ok_at:
                vals['nc_release_last_error'] = False       # a later successful release supersedes it
        elif code is None:
            state = 'unreachable'
            vals = {'nc_release_state': state, 'nc_release_detail': body.get('error') or ''}
        else:
            state = 'unknown'
            vals = {'nc_release_state': state, 'nc_release_detail': self.env._(
                "The tenant answered HTTP %s to the release status (its code predates the release gate, or the "
                "service key does not match).", code)}
        vals['nc_release_action'] = self._nc_release_action_text(state, body if code in (200, 409) else {}) or False
        vals['nc_release_checked_at'] = fields.Datetime.now()
        self.sudo().write(vals)
        return state

    def _nc_release_refresh(self):
        """Ask the tenant for its state now, record it, return it."""
        self.ensure_one()
        return self._nc_release_record(*self._nc_release_call(STATUS_PATH))

    def _nc_release_hold(self, hold, reason=''):
        """Hold the tenant (``upgrading``, ``upgrade_failed`` or ``blocked``): its users get the maintenance page."""
        self.ensure_one()
        code, body = self._nc_release_call(HOLD_PATH, {'hold': hold, 'reason': reason or ''})
        state = self._nc_release_record(code, body)
        if code != 200:
            raise UserError(self.env._("%(company)s could not be held for the release (%(state)s).",
                                       company=self.company_name, state=state))
        return state

    def _nc_release_open(self):
        """Lift a hold. The tenant refuses (HTTP 409) while the code is still ahead of its database."""
        self.ensure_one()
        code, body = self._nc_release_call(OPEN_PATH, {})
        return self._nc_release_record(code, body)

    # ------------------------------------------------------------------ operator action
    def action_nc_release_check(self):
        """Tenant form button: refresh the release state of these tenants. Platform staff only."""
        if not self.env.user.has_group(_PLATFORM_GROUP):
            raise AccessError(self.env._("Only a Platform Administrator can check a tenant's release state."))
        for tenant in self:
            tenant._nc_release_refresh()
        return True
