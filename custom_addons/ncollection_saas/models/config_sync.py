# -*- coding: utf-8 -*-
"""Workspace config sync & plan-change propagation (P2-T03).

When a subscription/tenant/plan changes on the platform, push the new
allowed_module_names + status + limits into the tenant DB's
ncollection.workspace.config so licensing (menus + ORM) tracks the plan within
the minute. Mechanism (ARCHITECTURE_SECURITY §11): a platform-initiated json2
call over loopback, authenticated with a dedicated, workspace.config-scoped
service account whose bearer key is derived PER TENANT from a platform master
(#212, see derive_tenant_key) — never a shared key, never a cross-DB SQL or ORM
cursor (Rule 3). Every sync is logged; a nightly cron reconciles drift.

Lives in ncollection_saas: only this layer may reach a tenant DB.
"""

import base64
import hashlib
import hmac
import json
import logging
import os

import requests

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# .env / secrets-store key (never in git or the DB) — the platform MASTER key from
# which each tenant's config-sync bearer key is derived (#212).
_SYNC_KEY_ENV = 'NC_CONFIG_SYNC_KEY'
# Domain-separation label for the key derivation (so the master can't be reused
# verbatim for another purpose).
_KDF_LABEL = b'nc-config-sync:'


def derive_tenant_key(master: str, db: str) -> str:
    """Per-tenant config-sync bearer key = HMAC-SHA256(master, label || db) (#212).

    HMAC from a high-entropy master is a standard KDF: every tenant gets a UNIQUE,
    one-way key, so a leaked/logged key authenticates against ONLY that tenant's DB
    (its stored hash matches only this derivation) — never platform-wide. It is
    deterministic, so the platform re-derives the exact key it seeded WITHOUT
    storing any per-tenant secret; the master alone lives in the secrets store, and
    rotating it rotates every tenant. Same helper is used by the provisioning seed
    (to store the hash) and here (to present the bearer), so they can never drift.
    """
    mac = hmac.new(master.encode(), _KDF_LABEL + db.encode(), hashlib.sha256)
    return base64.urlsafe_b64encode(mac.digest()).decode()


# Non-secret loopback base URL for the local Odoo (overridable per deployment).
_BASE_URL_PARAM = 'ncollection_saas.internal_base_url'
_DEFAULT_BASE_URL = 'http://localhost:8069'
# Base domain tenant subdomains hang off (<db>.<base-domain>). The loopback push
# targets a fixed IP:port, but the RECEIVING Odoo routes the DB by Host under
# db_filter=^%d$ (production + the routing stack) — so the request MUST present
# `Host: <db>.<base-domain>` or the tenant DB is rejected (no DB selected -> 404,
# and the sync silently no-ops). X-Odoo-Database does NOT bypass db_filter: it is
# itself filtered by the request Host (odoo/http.py). Same param the domain layer
# uses; only the first Host label matters to ^%d$, so the value is env-agnostic.
_BASE_DOMAIN_PARAM = 'ncollection_saas.base_domain'
_DEFAULT_BASE_DOMAIN = 'ncollectionerp.com'
_SYNC_ENDPOINT = '/json/2/ncollection.workspace.config/sync_from_platform'
_SYNC_CHANNEL = 'root.provisioning'
_RPC_TIMEOUT = 30

# HTTP statuses that a retry can never heal (#264). A 401/403 means the tenant
# rejected our bearer — a stale key hash, typically — so the nightly reconcile
# will retry forever without fixing anything. A timeout or a 5xx is the opposite:
# transient by nature and exactly what the reconcile exists to heal. Logging both
# identically, as this module did, made "will fix itself" indistinguishable from
# "needs a human" at the only moment that distinction matters.
_PERMANENT_STATUSES = frozenset({401, 403})

SYNC_STATES = [
    ('ok', 'In sync'),
    ('transient', 'Retrying'),
    ('permanent', 'Needs attention'),
]


class TenantConfigSync(models.Model):
    _inherit = 'ncollection.tenant'

    # ---- config-sync health (#264) ---------------------------------------
    #
    # Why durable state and not just a log line: config-sync is what propagates
    # action_suspend / action_expire / plan downgrades into the tenant's
    # ncollection.workspace.config, which P1-T10 license enforcement reads. A
    # push that fails means a SUSPENDED SUBSCRIPTION SILENTLY FAILS TO LOCK THE
    # WORKSPACE — the customer keeps working. P2-T10's log_watcher does alert on
    # ERROR lines, so this was never fully silent, but a generic "Odoo logged 3
    # ERROR lines in the last 5m" cannot say WHICH tenant, cannot be queried,
    # and misses anything outside its window. These fields make the failure
    # attributable and answerable from the admin UI.

    config_sync_state = fields.Selection(
        SYNC_STATES, default='ok', readonly=True, tracking=True,
        string='Config Sync', copy=False,
        help="Whether the last platform->tenant config push succeeded. "
             "'Needs attention' means the failure cannot heal itself.")
    config_sync_last_ok = fields.Datetime(
        readonly=True, copy=False, string='Config Sync Last OK',
        help="When config last reached this tenant successfully. Answers "
             "'how long has this been broken?' at a glance.")
    config_sync_last_error = fields.Char(
        readonly=True, copy=False, string='Config Sync Last Error')
    config_sync_failure_count = fields.Integer(
        default=0, readonly=True, copy=False,
        string='Consecutive Sync Failures')

    # ---- desired state ---------------------------------------------------

    def _config_sync_vals(self):
        """The config the tenant SHOULD have, from platform source-of-truth.

        subscription_status is projected from tenant.status (trial/active/
        suspended/expired) — the tenant's effective access state, which alone
        carries 'suspended' (subscription.status does not). This is the value
        the tenant-side interstitial gate keys on."""
        self.ensure_one()
        plan = self.plan_id
        return {
            'allowed_module_names': (plan.allowed_module_names or '') if plan else '',
            'plan_code': (plan.code or '') if plan else '',
            'subscription_status': self.status or 'active',
            'max_users': (plan.max_users if plan else 0) or 0,
        }

    # ---- trigger + async -------------------------------------------------

    def _config_sync_enqueue(self):
        """Enqueue a config push for the ready tenants in self (off HTTP
        workers, on the provisioning channel). No-op for tenants without a
        ready DB — their config is written by provisioning's seed."""
        for tenant in self:
            if tenant.database_status == 'ready' and tenant.database_name:
                tenant.with_delay(
                    channel=_SYNC_CHANNEL,
                    description="Sync workspace config -> '%s'" % tenant.database_name,
                    identity_key='nc-config-sync-%s' % tenant.id,
                ).sync_workspace_config()

    def sync_workspace_config(self):
        """Push desired config into each ready tenant DB (queue_job target)."""
        for tenant in self:
            if tenant.database_status != 'ready' or not tenant.database_name:
                continue
            tenant._config_sync_push(tenant.database_name, tenant._config_sync_vals())
        return True

    # ---- outcome recording + alerting (#264) -----------------------------

    def _config_sync_record(self, state, error=None):
        """Record a push outcome on the tenant. NEVER raises into the caller.

        `_config_sync_push` promises not to break a lifecycle transaction, and
        that promise has to survive this bookkeeping too — observability code
        that can break a suspension is worse than no observability.
        """
        try:
            self.ensure_one()
            previous = self.config_sync_state
            if state == 'ok':
                vals = {
                    'config_sync_state': 'ok',
                    'config_sync_last_ok': fields.Datetime.now(),
                    'config_sync_last_error': False,
                    'config_sync_failure_count': 0,
                }
                self.sudo().write(vals)
                if previous and previous != 'ok':
                    self.sudo().message_post(body=self.env._(
                        "Config sync RECOVERED for %(db)s.",
                        db=self.database_name))
                return

            self.sudo().write({
                'config_sync_state': state,
                'config_sync_last_error': (error or '')[:255],
                'config_sync_failure_count': self.config_sync_failure_count + 1,
            })
            # Alert on the TRANSITION, not on every retry. The nightly reconcile
            # touches every ready tenant, so a permanent 401 would otherwise post
            # a chatter message and open an activity every single night, forever
            # — which trains everyone to ignore the channel.
            escalated = previous != state and state == 'permanent'
            if previous == 'ok' or escalated:
                self._config_sync_alert(state, error)
        except Exception:  # pragma: no cover - defensive: never break a push
            _logger.exception(
                "Could not record config-sync outcome for %s (state=%s).",
                self.database_name if self else '?', state)

    def _config_sync_alert(self, state, error):
        """Chatter + activity on the tenant, mirroring P2-T05's backup alert.

        Deliberately the SAME mechanism backup failures already use
        (ncollection_saas/models/backup.py::_alert_failure) rather than a new
        one: ncollection.tenant already carries mail.thread and
        mail.activity.mixin, operators already watch this surface, and one
        alerting convention beats two.
        """
        body = self.env._(
            "Config sync FAILED for %(db)s (%(state)s): %(error)s",
            db=self.database_name, state=state, error=error or 'unknown')
        self.sudo().message_post(body=body)
        if state != 'permanent':
            # A transient failure is logged and tracked, but the nightly
            # reconcile is expected to heal it — no human task for that.
            return
        # Never stack duplicate to-dos for the same unresolved problem.
        existing = self.sudo().activity_ids.filtered(
            lambda a: a.summary and 'config sync' in a.summary.lower())
        if existing:
            return
        self.sudo().activity_schedule(
            'mail.mail_activity_data_todo',
            summary=self.env._("Investigate config sync: %s", self.database_name),
            note=self.env._(
                "%(body)s\n\nThis failure cannot heal itself — the nightly "
                "reconcile will keep retrying without success until the cause "
                "is fixed (typically a stale per-tenant key; see #221).",
                body=body))

    # ---- the json2/bearer client ----------------------------------------

    def _config_sync_push(self, db, vals):
        """One platform->tenant config push over json2/bearer. Logged; never
        raises into the caller (a transport error must not break a lifecycle
        transaction — the nightly reconcile heals it)."""
        master = os.environ.get(_SYNC_KEY_ENV)
        if not master:
            _logger.error(
                "Config sync SKIPPED for %s: %s is not set (secrets store/.env).",
                db, _SYNC_KEY_ENV)
            self._config_sync_record(
                'permanent', "%s is not set" % _SYNC_KEY_ENV)
            return False
        base = self.env['ir.config_parameter'].sudo().get_param(
            _BASE_URL_PARAM, _DEFAULT_BASE_URL)
        base_domain = (self.env['ir.config_parameter'].sudo().get_param(
            _BASE_DOMAIN_PARAM, _DEFAULT_BASE_DOMAIN) or '').strip().lower()
        try:
            # Derived here as part of the push attempt. It is total on (str, str)
            # — master is guarded non-empty above, db is the sanitized
            # database_name — so it cannot raise in practice; keeping it inside
            # the try keeps the whole attempt under one failure boundary.
            key = derive_tenant_key(master, db)  # per-tenant bearer (#212)
            resp = requests.post(
                base + _SYNC_ENDPOINT,
                headers={
                    # Loopback connects to a fixed IP:port, but the receiver
                    # selects the DB by Host under db_filter=^%d$ — present the
                    # tenant subdomain so ITS db (not 'localhost') is chosen.
                    # X-Odoo-Database must agree (it, too, is Host-filtered).
                    'Host': '%s.%s' % (db, base_domain),
                    'Authorization': 'Bearer %s' % key,
                    'X-Odoo-Database': db,
                    'Content-Type': 'application/json',
                },
                data=json.dumps({'vals': vals}),
                timeout=_RPC_TIMEOUT,
            )
        except requests.RequestException as exc:
            _logger.error("Config sync to %s failed (transport): %s", db, exc)
            self._config_sync_record('transient', "transport: %s" % exc)
            return False
        if resp.status_code != 200:
            permanent = resp.status_code in _PERMANENT_STATUSES
            # The log line now names the tenant and says whether a retry can
            # help, so P2-T10's generic ERROR watcher quotes something useful
            # in its alert instead of "Odoo logged 3 ERROR lines".
            _logger.error(
                "Config sync to %s failed (HTTP %s, %s): %s",
                db, resp.status_code,
                'PERMANENT - needs attention' if permanent else 'transient',
                resp.text[:500])
            self._config_sync_record(
                'permanent' if permanent else 'transient',
                "HTTP %s" % resp.status_code)
            return False
        _logger.info("Config sync -> %s ok: plan=%s status=%s modules=%r",
                     db, vals.get('plan_code'), vals.get('subscription_status'),
                     vals.get('allowed_module_names'))
        self._config_sync_record('ok')
        return True

    # ---- lifecycle triggers (status → subscription_status projection) ----

    def action_suspend(self):
        res = super().action_suspend()
        self._config_sync_enqueue()  # suspends the workspace (interstitial)
        return res

    def action_activate(self):
        res = super().action_activate()
        self._config_sync_enqueue()  # lifts the interstitial
        return res

    def action_expire(self):
        res = super().action_expire()
        self._config_sync_enqueue()
        return res

    # ---- nightly reconciliation cron ------------------------------------

    @api.model
    def _cron_reconcile_config(self):
        """Re-push desired config to every ready tenant, healing drift
        (tampering, a missed live sync, a failed push). Idempotent."""
        tenants = self.search([('database_status', '=', 'ready'),
                               ('database_name', '!=', False)])
        _logger.info("Config reconcile: %s ready tenant(s)", len(tenants))
        tenants._config_sync_enqueue()
        return True


class SubscriptionPlanConfigSync(models.Model):
    _inherit = 'ncollection.subscription.plan'

    def write(self, vals):
        """A plan edit (module set / seat cap) propagates to every ready tenant
        on that plan (plan upgrade/downgrade at the plan level)."""
        res = super().write(vals)
        if 'allowed_module_names' in vals or 'max_users' in vals:
            self.mapped('tenant_ids')._config_sync_enqueue()
        return res
