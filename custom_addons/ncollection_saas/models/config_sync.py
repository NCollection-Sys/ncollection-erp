# -*- coding: utf-8 -*-
"""Workspace config sync & plan-change propagation (P2-T03).

When a subscription/tenant/plan changes on the platform, push the new
allowed_module_names + status + limits into the tenant DB's
ncollection.workspace.config so licensing (menus + ORM) tracks the plan within
the minute. Mechanism (ARCHITECTURE_SECURITY §11): a platform-initiated json2
call over loopback, authenticated with a dedicated, workspace.config-scoped
service account (bearer API key from the secrets store / .env) — never a
cross-DB SQL or ORM cursor (Rule 3). Every sync is logged; a nightly cron
reconciles drift.

Lives in ncollection_saas: only this layer may reach a tenant DB.
"""

import json
import logging
import os

import requests

from odoo import api, models

_logger = logging.getLogger(__name__)

# .env / secrets-store key (never in git or the DB) — the shared bearer key of
# the per-tenant config-sync service account.
_SYNC_KEY_ENV = 'NC_CONFIG_SYNC_KEY'
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


class TenantConfigSync(models.Model):
    _inherit = 'ncollection.tenant'

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

    # ---- the json2/bearer client ----------------------------------------

    def _config_sync_push(self, db, vals):
        """One platform->tenant config push over json2/bearer. Logged; never
        raises into the caller (a transport error must not break a lifecycle
        transaction — the nightly reconcile heals it)."""
        key = os.environ.get(_SYNC_KEY_ENV)
        if not key:
            _logger.error(
                "Config sync SKIPPED for %s: %s is not set (secrets store/.env).",
                db, _SYNC_KEY_ENV)
            return False
        base = self.env['ir.config_parameter'].sudo().get_param(
            _BASE_URL_PARAM, _DEFAULT_BASE_URL)
        base_domain = (self.env['ir.config_parameter'].sudo().get_param(
            _BASE_DOMAIN_PARAM, _DEFAULT_BASE_DOMAIN) or '').strip().lower()
        try:
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
            return False
        if resp.status_code != 200:
            _logger.error("Config sync to %s failed (HTTP %s): %s",
                          db, resp.status_code, resp.text[:500])
            return False
        _logger.info("Config sync -> %s ok: plan=%s status=%s modules=%r",
                     db, vals.get('plan_code'), vals.get('subscription_status'),
                     vals.get('allowed_module_names'))
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
