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
import re
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

# The tenant's health report is TENANT-CONTROLLED input. An honest tenant only
# ever sends the xml-ids its own module declares, but a compromised one could
# stuff this dict with fabricated or oversized job names that then land in the
# platform's admin UI and chatter.
#
# The isolation audit suggested allowlisting against ncollection_core's
# REQUIRED_CRON_XMLIDS — but ncollection_saas does not depend on
# ncollection_core (its depends are subscription/branding/queue_job), so that
# constant is not reachable across the boundary. Bounding the SHAPE achieves the
# same defence without inventing a dependency the architecture does not have:
# a plausible xml-id, a sane length, and a cap on how many we will believe.
_XMLID_RE = re.compile(r'^[a-z0-9_]{1,64}\.[a-z0-9_]{1,64}$')
_MAX_REPORTED_CRONS = 32

CRON_HEALTH_STATES = [
    ('ok', 'Healthy'),
    ('degraded', 'A required job is off'),
    ('unknown', 'Not reported'),
]

SYNC_STATES = [
    ('ok', 'In sync'),
    ('transient', 'Retrying'),
    ('permanent', 'Needs attention'),
]


class TenantConfigSync(models.Model):
    _inherit = 'ncollection.tenant'

    # ---- config-sync health (#264) ---------------------------------------
    #
    # Declared here rather than on the base model in ncollection_subscription
    # because config sync is a SaaS-layer concern — the same reasoning
    # checkout.py uses for its checkout_* fields. Odoo tracks field ownership
    # per defining module, so uninstalling ncollection_saas drops these columns
    # cleanly without touching the subscription module's own fields.
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
        SYNC_STATES, default='ok', readonly=True,
        string='Config Sync', copy=False,
        # No tracking=True: this model is a mail.thread, so tracking would
        # auto-post a second chatter line for every transition on top of the
        # explicit message_post below. backup.py's status field omits it for
        # the same reason. Two entries per event trains operators to skim.
        help="Whether the last platform->tenant config push succeeded. "
             "'Needs attention' means the failure cannot heal itself.")
    config_sync_last_ok = fields.Datetime(
        readonly=True, copy=False, string='Config Sync Last OK',
        help="When config last reached this tenant successfully. Answers "
             "'how long has this been broken?' at a glance.")
    config_sync_last_error = fields.Char(readonly=True, copy=False)
    config_sync_failure_count = fields.Integer(
        default=0, readonly=True, copy=False,
        string='Consecutive Sync Failures')
    # ---- required tenant-side cron health (#262) -------------------------
    #
    # Odoo deactivates a cron itself after 5 failures spanning 7 days, so a
    # misconfigured compliance job (e.g. the auth-log retention purge, #219)
    # eventually switches OFF and goes quiet. The tenant self-reports on the
    # config-sync response; the platform is the only place that can see the
    # whole fleet, so it is where the watching belongs.
    cron_health_state = fields.Selection(
        CRON_HEALTH_STATES, default='unknown', readonly=True, copy=False,
        string='Required Jobs',
        help="Whether the tenant-side jobs the platform depends on are still "
             "enabled. 'Not reported' means no usable report: the tenant has "
             "not synced since this check shipped, its response body could not "
             "be read, or it could not run the probe. It does NOT mean "
             "healthy.")
    cron_health_detail = fields.Char(readonly=True, copy=False)
    cron_health_activity_id = fields.Many2one(
        'mail.activity', readonly=True, copy=False, ondelete='set null',
        string='Open Required-Job To-Do')
    config_sync_activity_id = fields.Many2one(
        'mail.activity', readonly=True, copy=False, ondelete='set null',
        string='Open Config-Sync To-Do',
        help="The unresolved config-sync to-do, if any. An explicit link "
             "rather than searching activity summaries: the summary is "
             "translated, so a substring match silently stops working on a "
             "non-English backoffice.")

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

    # ---- shared health-activity lifecycle (#262/#264) ---------------------
    #
    # Extracted because the duplication ALREADY cost correctness: the config-sync
    # and required-cron recorders were meant to share the rule "recovery closes
    # the to-do", and only one of them had it. The cron copy closed only on
    # degraded -> ok, so degraded -> unknown -> ok left the to-do open forever —
    # and a stale to-do makes the NEXT real incident look already-reported and
    # swallows it. That is exactly the bug #264's review caught, reappearing in
    # the parallel implementation.
    #
    # Deliberately narrow: this shares the ACTIVITY lifecycle, not the two state
    # machines, which genuinely differ (one classifies HTTP outcomes, the other
    # a tenant's self-report).

    def _health_open_activity(self, field, summary, note):
        """Open a to-do for `field`, unless one is already open."""
        existing = self.sudo()[field]
        if existing and existing.exists():
            return
        self.sudo()[field] = self.sudo().activity_schedule(
            'mail.mail_activity_data_todo', summary=summary, note=note)

    def _health_close_activity(self, field, feedback):
        """Resolve the to-do for `field`. Idempotent — safe to call always.

        Being safe to call unconditionally is the point: every recovery path
        calls it without first reasoning about which state it came from.
        """
        activity = self.sudo()[field]
        if activity and activity.exists():
            activity.action_feedback(feedback=feedback)
        self.sudo()[field] = False

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
                # Close the open to-do. Without this it lingers after recovery,
                # and the guard in _config_sync_alert then treats a genuinely
                # NEW failure weeks later as already-reported — no fresh to-do
                # is opened and the only actionable channel goes quiet. That is
                # the swallowed-failure mode this ticket exists to prevent,
                # one layer up.
                self._config_sync_close_activity()
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

    def _config_sync_close_activity(self):
        """Resolve the open config-sync to-do, if there is one."""
        self._health_close_activity('config_sync_activity_id', self.env._(
            "Config sync recovered for %(db)s.", db=self.database_name))

    # ---- required-cron health (#262) -------------------------------------

    def _record_cron_health(self, report):
        """Record the tenant's self-report on required jobs. Never raises.

        `report` is ``{xml_id: {'installed': bool, 'active': bool}}`` from the
        tenant's sync response, or None when this tenant did not report (older
        tenant code, or a body we could not parse).

        A cron whose module is NOT installed is deliberately NOT a fault: a
        tenant provisioned before that module existed legitimately lacks it,
        and #218 is the ticket that backfills those. Alerting on it here would
        be noise that trains people to ignore the signal.
        """
        try:
            self.ensure_one()
            if not isinstance(report, dict) or not report:
                # Absent report != healthy. Say "unknown" rather than assume.
                if self.cron_health_state != 'unknown':
                    self.sudo().write({'cron_health_state': 'unknown'})
                return

            # A job whose module is absent is NOT a fault (#218 backfills
            # those). A probe that ERRORED on the tenant is a different thing
            # again: the tenant could not answer, so we must not read it as
            # healthy — it shipped an `error` flag that was previously computed,
            # sent over the wire, and then never consulted.
            # Keep only entries that look like a real xml-id, and only as many
            # as we are willing to believe. Anything else is silently ignored
            # rather than rendered into an operator's chatter.
            report = {
                xml_id: info
                for xml_id, info in sorted(report.items())[:_MAX_REPORTED_CRONS]
                if isinstance(xml_id, str) and _XMLID_RE.match(xml_id)
                and isinstance(info, dict)
            }
            if not report:
                if self.cron_health_state != 'unknown':
                    self.sudo().write({'cron_health_state': 'unknown'})
                return

            errored = [
                xml_id for xml_id, info in report.items()
                if isinstance(info, dict) and info.get('error')
            ]
            broken = [
                xml_id for xml_id, info in report.items()
                if isinstance(info, dict) and not info.get('error')
                and info.get('installed') and not info.get('active')
            ]
            previous = self.cron_health_state
            if broken:
                detail = self.env._(
                    "Disabled on the tenant: %(jobs)s", jobs=', '.join(sorted(broken)))
                self.sudo().write({'cron_health_state': 'degraded',
                                   'cron_health_detail': detail[:255]})
                if previous != 'degraded':
                    self._alert_cron_health(detail)
                return

            if errored:
                # Could not answer != healthy.
                self.sudo().write({
                    'cron_health_state': 'unknown',
                    'cron_health_detail': self.env._(
                        "Tenant could not read: %(jobs)s",
                        jobs=', '.join(sorted(errored)))[:255]})
                return

            self.sudo().write({'cron_health_state': 'ok',
                               'cron_health_detail': False})
            if previous == 'degraded':
                self.sudo().message_post(body=self.env._(
                    "Required tenant jobs are enabled again on %(db)s.",
                    db=self.database_name))
            # UNCONDITIONAL. Closing only on degraded->ok left the to-do open
            # across degraded->unknown->ok, and a stale to-do makes the next
            # real incident look already-reported.
            self._close_cron_health_activity()
        except Exception:  # pragma: no cover - defensive: never break a push
            _logger.exception(
                "Could not record required-cron health for %s.",
                self.database_name if self else '?')

    def _alert_cron_health(self, detail):
        """Chatter + one to-do, same convention as the config-sync alert."""
        body = self.env._(
            "A required job is DISABLED on %(db)s: %(detail)s\n\n"
            "Odoo deactivates a cron by itself after repeated failures, so this "
            "is usually a symptom rather than someone switching it off. The "
            "job stays off until re-enabled by hand.",
            db=self.database_name, detail=detail)
        self.sudo().message_post(body=body)
        self._health_open_activity(
            'cron_health_activity_id',
            self.env._("Re-enable required job: %s", self.database_name),
            body)

    def _close_cron_health_activity(self):
        self._health_close_activity('cron_health_activity_id', self.env._(
            "Required jobs healthy again on %(db)s.", db=self.database_name))

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
        # Never stack duplicate to-dos for the same unresolved problem. Keyed
        # on an explicit link, not a summary substring: the summary is
        # translated, so a match on "config sync" silently stops working on an
        # Arabic backoffice and starts stacking duplicates — and it could also
        # be suppressed by any unrelated activity that happens to mention it.
        self._health_open_activity(
            'config_sync_activity_id',
            self.env._("Investigate config sync: %s", self.database_name),
            self.env._(
                "%(body)s\n\nThis failure cannot heal itself — the nightly "
                "reconcile will keep retrying without success until the cause "
                "is fixed (typically a stale per-tenant key; see #221).",
                body=body))

    # ---- the json2/bearer client ----------------------------------------

    def _config_sync_push(self, db, vals):
        """One platform->tenant config push over json2/bearer. Logged; never
        raises into the caller (a transport error must not break a lifecycle
        transaction — the nightly reconcile heals it)."""
        # The outcome is recorded on `self` while the push targets `db` (Host
        # header + derived key). Every call site passes them matched, but the
        # invariant was convention-only — and this ticket raises the stakes: a
        # mismatch would now also post chatter and open a to-do on the WRONG
        # tenant, i.e. a cross-tenant attribution bug wearing an audit trail.
        if self and db and self.database_name and db != self.database_name:
            _logger.error(
                "Config sync attribution mismatch: pushing to %s while "
                "recording on %s. Refusing.", db, self.database_name)
            return False

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
        # The tenant self-reports required-job health on this response (#262).
        # Parsing is wrapped: json2 returns the method result unwrapped, but a
        # malformed or truncated body must never turn a SUCCESSFUL config push
        # into a failure.
        try:
            payload = resp.json()
        except Exception:
            # Broad on purpose. The contract is that parsing must NEVER turn a
            # SUCCESSFUL push into a failure, and the failure modes are not
            # just malformed JSON: a truncated body, a proxy returning HTML, or
            # any response object that does not implement .json(). Catching only
            # ValueError was too narrow and broke seven existing tests the first
            # time this ran — which is exactly the contract those tests defend.
            _logger.warning(
                "Config sync to %s returned an unreadable body; skipping the "
                "required-job health report.", db)
            payload = None
        self._record_cron_health(
            (payload or {}).get('required_crons')
            if isinstance(payload, dict) else None)
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
