# -*- coding: utf-8 -*-
"""Provisioning engine (P2-T01) — turns a queued ncollection.provisioning.job
into a login-ready tenant database, or rolls back cleanly.

Isolation (ARCHITECTURE_DATA_PLATFORM §10, ARCHITECTURE_SECURITY §11):
- Runs OFF the HTTP workers via OCA queue_job (`action_run` -> with_delay).
- NEVER opens an ORM cursor on a tenant DB from the admin process (Rule 3):
  the tenant DB is created and seeded through isolated `odoo` SUBPROCESSES;
  rollback drops it via a direct psycopg2 maintenance connection.
- Secure by default: strict name sanitisation + reserved-word list + collision
  check + per-hour quota; the tenant admin is seeded with a forced password
  reset (no known initial password).
"""

import logging
import os
import re
from datetime import timedelta

import psycopg2

from odoo import fields, models
from odoo.exceptions import UserError, ValidationError

# Intra-package (platform-layer) import — the KDF + master env-var name live with
# the config-sync push code. The PLATFORM derives each tenant's config-sync key and
# hands only the derived value to the seed subprocess, so the master never enters
# the tenant context and the seed needs no cross-package import (#212).
from .config_sync import _SYNC_KEY_ENV, derive_tenant_key
from .saas_subprocess import DB_NAME_RE, RESERVED_DB_NAMES

_logger = logging.getLogger(__name__)

# The seed receives the already-derived per-tenant config-sync bearer under this
# name (NOT the master). Keeps the master out of the tenant subprocess env (#212).
_SEED_TENANT_KEY_ENV = 'NC_CONFIG_SYNC_TENANT_KEY'

# DB_NAME_RE (strict: lowercase alphanumeric, letter-initial, 3–63 chars, NO
# underscore) and RESERVED_DB_NAMES (ARCHITECTURE_DATA_PLATFORM §2) are imported
# from the mixin above — one definition, so the allowlist cannot drift (#243).
#
# _CLEANUP_NAME_RE is a PERMISSIVE variant that stays local because it is used by
# the DESTRUCTIVE cleanup guards only (retry-sweep + rollback). Those must be able
# to drop a database THIS engine could have created — including a legacy underscore
# name left by a job from before DB_NAME_RE was tightened (#211). Validation stays
# strict via DB_NAME_RE; cleanup must not strand a zombie DB just because the format
# rule got stricter after that DB was created (#214).
_CLEANUP_NAME_RE = re.compile(r'^[a-z][a-z0-9_]{2,62}$')

# Always installed into a tenant DB (base + license/config + branding + auth
# hardening). ncollection_auth (P1-T19) gives every tenant the idle-session-timeout
# logout + the ncollection.auth.log login-audit trail by default — baseline security,
# not a paid tier (#178). Forward-only: existing tenants are backfilled via #218.
# Its deps (auth_signup, auth_session_timeout, web) are pulled automatically by Odoo
# and are already on the addons path (OCA server-auth).
CORE_TENANT_MODULES = ('base', 'ncollection_core', 'ncollection_branding', 'ncollection_auth')

QUOTA_PARAM = 'ncollection_saas.provisioning_quota_per_hour'
DEFAULT_QUOTA = 20
PROVISION_CHANNEL = 'root.provisioning'
SEED_SCRIPT = os.path.join(
    os.path.dirname(__file__), '..', 'scripts', 'provisioning', 'seed_tenant.py'
)
# NOTE: the 30-min subprocess cap now lives with the runner it belongs to,
# saas_subprocess.SUBPROCESS_TIMEOUT. A copy left here would be read as
# authoritative and edited by someone expecting it to take effect — which is
# precisely the silent-divergence bug #243 exists to remove.


class ProvisioningJob(models.Model):
    # The subprocess mixin carries the isolated-odoo + maintenance-DB
    # primitives (#243). Before this, provisioning kept its own copies and a
    # guard-test policed the two for drift; one definition removes the class of
    # bug the guard existed for.
    # _name is REQUIRED here, not decorative. odoo/models.py MetaModel.__new__
    # only defaults _name to _inherit when _inherit is a STRING:
    #     if _inherit and isinstance(_inherit, str):
    #         attrs.setdefault('_name', _inherit)
    # With a LIST and no _name it falls through to deriving the name from the
    # CLASS name — ProvisioningJob -> 'provisioning.job' — so this class would
    # silently define a brand-new, empty model instead of extending
    # ncollection.provisioning.job. It surfaced as "action_run is not a valid
    # action" when the view loaded, not as an import error.
    # Odoo core follows the same rule: mail/models/res_partner.py declares
    # _name = 'res.partner' explicitly even though _inherit[0] is 'res.partner'.
    # With a LIST _inherit, always set _name — there is no exception.
    _name = 'ncollection.provisioning.job'
    _inherit = ['ncollection.provisioning.job',
                'ncollection.saas.subprocess.mixin']

    # ---- entry points ----------------------------------------------------

    def action_run(self):
        """Enqueue provisioning on the dedicated queue channel (off HTTP workers).

        identity_key de-duplicates: while a job is already pending on the queue,
        a second enqueue for the same job is dropped — closing the double-run
        window that could otherwise let one run's fresh DB be dropped by another.
        """
        for job in self:
            job._check_quota()
            job.with_delay(
                channel=PROVISION_CHANNEL,
                description=self.env._("Provision tenant DB '%s'", job.database_name),
                identity_key='nc-provision-job-%s' % job.id,
            ).run_provisioning()
        return True

    def action_run_sync(self):
        """Run the engine inline (manual button / tests). Same engine, no queue."""
        for job in self:
            job.run_provisioning()
        return True

    # ---- the engine ------------------------------------------------------

    def run_provisioning(self):
        """Validate -> create DB -> seed -> done, rolling back on any failure.

        Public so OCA queue_job can serialise + call it in the runner worker.

        Safety (P2-T02): only the DB THIS run creates is ever dropped on
        failure — a pre-existing database (a live tenant, or the platform DB
        itself) is never touched. A retry of a failed job first clears the
        zombie left by its own previous attempt, so retries heal cleanly.
        """
        self.ensure_one()
        # Status guard: never re-run a completed job; skip one already running
        # under another worker (visible once that run commits).
        if self.status == 'done':
            raise UserError(self.env._(
                "Job for database '%s' is already done; refusing to re-run.", self.database_name))
        if self.status == 'running':
            return True
        retry = self.status == 'failed'
        db = self.database_name
        self._set_status('running', self.env._("Provisioning started for database '%s'.", db))
        # sudo(): authorises the guarded 'provisioning'/'ready' status transitions
        # (ncollection.tenant, #228). env.su is not RPC-spoofable, unlike a context
        # key — the engine is trusted platform code.
        self.tenant_id.sudo().write({'database_status': 'provisioning'})
        created = False
        try:
            if retry and self._safe_to_drop(db):
                # This job failed before and left a database behind (rollback
                # could not complete). It is this job's own DB — drop it so the
                # retry starts from a clean slate.
                self._drop_database(db)
                self._append_log(self.env._(
                    "Retry: cleared the database left by a previous failed run."))

            self._validate_db_name(db)
            self._append_log(self.env._("Name validated."))

            modules = self._module_list()
            created = True  # from here on, a failure owns the DB we are creating
            setup_url = None
            self._run_odoo_init(db, modules)
            self._append_log(
                self.env._("Database created; modules installed: %s", ', '.join(modules)))

            setup_url = self._seed_tenant(db)
            self._append_log(
                self.env._("Tenant seeded: admin (forced reset), workspace config, branding."))

            self._mark_done(setup_url=setup_url)
        except Exception as exc:  # noqa: BLE001 - engine must catch to roll back
            # Record the failure WITHOUT re-raising: re-raising would make both
            # the queue runner and the HTTP button roll the transaction back and
            # lose the 'failed' status, the log, and the admin alert (leaving the
            # job stuck at 'running'). Returning normally lets the transaction
            # commit the recorded failure. The failed job stays retryable via
            # the Provision button. Programmatic callers get False.
            self._persist_failure(db, created, exc)
            return False
        return True

    # ---- steps -----------------------------------------------------------

    def _validate_db_name(self, db):
        """Reject injection / overwrite / reserved names before any side effect.

        DELIBERATELY NOT the mixin's ``_assert_safe_db_name`` (#243). They look
        like duplicates and encode OPPOSITE requirements about existence:

          * this one CREATES a database, so an existing name is a collision and
            must be rejected (the ``_database_exists`` check below);
          * the mixin's guard is used by fleet migration, which UPGRADES
            databases that must ALREADY exist — rejecting them would refuse
            every real target.

        Unifying them silently breaks one caller or the other, and neither
        failure is loud: provisioning would build over a live tenant DB, or a
        fleet migration would refuse to run. The shared parts (the regex, the
        reserved set, the subprocess and maintenance-DB helpers) ARE unified —
        it is only this existence rule that differs.
        """
        if not db or not DB_NAME_RE.match(db):
            raise ValidationError(self.env._(
                "Invalid database name '%s': must match ^[a-z][a-z0-9]{2,62}$.", db))
        if db in RESERVED_DB_NAMES or db == self.env.cr.dbname:
            # Never let a tenant name collide with a reserved word or the
            # platform DB itself (the platform DB is not in the static reserved
            # set because it is deployment-specific — read it at runtime).
            raise ValidationError(self.env._("Database name '%s' is reserved.", db))
        if self._database_exists(db):
            raise ValidationError(self.env._("Database '%s' already exists (collision).", db))

    def _module_list(self):
        """Core tenant modules + the plan's allowed modules (deduped, ordered)."""
        modules = list(CORE_TENANT_MODULES)
        plan = self.tenant_id.plan_id
        if plan:
            for m in plan.get_allowed_module_list():
                if m not in modules:
                    modules.append(m)
        return modules

    def _run_odoo_init(self, db, modules):
        """Create + initialise the DB in an ISOLATED odoo subprocess."""
        cmd = ['odoo'] + self._odoo_conn_args(db) + [
            '-i', ','.join(modules),
            '--without-demo=True', '--stop-after-init', '--no-http',
            '--max-cron-threads=0',
        ]
        self._run_odoo_subprocess(cmd, self.env._("database init"))

    def _seed_tenant(self, db):
        """Seed admin (forced reset) + workspace.config + branding via odoo shell.

        Runs in an isolated subprocess (no cross-DB ORM from the admin process).
        Tenant data is passed through the environment to the seed script.
        Returns the password-setup URL the seed printed (or None) so the caller
        can put it in the welcome email — computed in the tenant DB, where the
        reset token is valid.
        """
        tenant = self.tenant_id
        plan = tenant.plan_id
        with open(SEED_SCRIPT, encoding='utf-8') as fh:
            script = fh.read()
        env_vars = os.environ.copy()
        # #212: derive the per-tenant config-sync bearer HERE (platform side, where
        # the master legitimately lives) and pass ONLY the derived value to the seed.
        # Scrub the master from the subprocess env so it never enters the tenant
        # context; the seed just stores the hash of what it is handed — no KDF, no
        # cross-package import back into the platform addon.
        master = env_vars.pop(_SYNC_KEY_ENV, None)
        env_vars.update({
            'NC_COMPANY': tenant.company_name or 'Tenant',
            'NC_ADMIN_EMAIL': tenant.email or '',
            'NC_ALLOWED_MODULES': plan.allowed_module_names if plan else '',
            'NC_PLAN_CODE': plan.code if plan else '',
            'NC_MAX_USERS': str(plan.max_users if plan else 1),
            # Project the TENANT status (trial/active/suspended/expired) — the
            # effective access state P2-T03's sync + interstitial key on, and the
            # only enum that carries 'suspended'. Keeps the initial seed and the
            # ongoing config sync consistent (no first-reconcile drift).
            'NC_SUB_STATUS': tenant.status or 'active',
            'NC_PORTAL_URL': tenant.portal_url or self._portal_url(db),
        })
        if master:
            env_vars[_SEED_TENANT_KEY_ENV] = derive_tenant_key(master, db)
        # `shell` MUST be the first argument (odoo <subcommand> <options>).
        cmd = ['odoo', 'shell'] + self._odoo_conn_args(db) + ['--log-level=error']
        out = self._run_odoo_subprocess(
            cmd, self.env._("tenant seed"), stdin=script, env=env_vars)
        return self._parse_setup_url(out)

    @staticmethod
    def _parse_setup_url(stdout):
        """Extract the SEED_SETUP_URL=<url> line the seed script prints, if any."""
        for line in (stdout or '').splitlines():
            if line.startswith('SEED_SETUP_URL='):
                return line[len('SEED_SETUP_URL='):].strip() or None
        return None

    def _safe_to_drop(self, db):
        """Destructive-cleanup gate. Only ever drop a name that (a) this engine
        could have created, (b) is NOT a reserved word or the platform DB itself,
        and (c) actually exists. The reserved/self-db exclusion is load-bearing for
        the retry-sweep, which runs BEFORE _validate_db_name: a job manually created
        with database_name == the platform DB must never let a retry drop it."""
        return bool(
            db and _CLEANUP_NAME_RE.match(db)
            and db not in RESERVED_DB_NAMES and db != self.env.cr.dbname
            and self._database_exists(db))

    def _rollback(self, db, created):
        """Drop the half-provisioned DB — but ONLY if this run created it.

        `created` is True only once the engine has passed name validation and
        started _run_odoo_init, i.e. the database is one we are building. A
        failure BEFORE that (e.g. a name collision with an existing database)
        leaves `created` False, so a pre-existing tenant — or the platform DB —
        is never dropped (P2-T02 data-loss guard; SECURITY §11).
        """
        if not created:
            return
        try:
            if self._safe_to_drop(db):
                self._drop_database(db)
                self._append_log(self.env._("Rolled back: dropped database '%s'.", db))
        except Exception as exc:  # noqa: BLE001
            self._append_log(self.env._(
                "Rollback WARNING: could not drop %(db)s: %(err)s", db=db, err=exc))
            _logger.exception("Rollback failed for %s", db)

    def _persist_failure(self, db, created, exc):
        """Drop the DB we own (never a pre-existing one), record the failure on
        the job + tenant, and alert the admins. The caller returns instead of
        re-raising so the transaction commits this state."""
        self._rollback(db, created)
        self._append_log(self.env._("FAILED: %s", exc))
        self.write({'status': 'failed', 'completed_at': fields.Datetime.now()})
        self.tenant_id.write({'database_status': 'error'})
        self.tenant_id._notify_provisioning_failure(self.log)
        _logger.warning("Provisioning failed for database %s: %s", db, exc)

    # ---- infrastructure helpers -----------------------------------------

    def _database_exists(self, db):
        conn = psycopg2.connect(**self._db_conn_params('postgres'))
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db,))
            return cur.fetchone() is not None
        finally:
            conn.close()

    # ---- quota + status --------------------------------------------------

    def _check_quota(self):
        limit = int(self.env['ir.config_parameter'].sudo().get_param(QUOTA_PARAM, DEFAULT_QUOTA))
        if limit <= 0:
            return  # cap disabled
        since = fields.Datetime.now() - timedelta(hours=1)
        recent = self.search_count([('created_at', '>=', since)])
        if recent > limit:
            raise UserError(self.env._(
                "Provisioning quota exceeded: %(n)s jobs in the last hour (limit %(limit)s).",
                n=recent, limit=limit))

    def _set_status(self, status, message):
        self.write({'status': status})
        self._append_log(message)

    def _append_log(self, message):
        stamp = fields.Datetime.to_string(fields.Datetime.now())
        line = "[%s] %s" % (stamp, message)
        self.log = (self.log + "\n" + line) if self.log else line

    def _portal_url(self, db):
        """The tenant's workspace URL. Dev default routes by subdomain on
        localhost; P2-T06 productionises real domains/SSL. Overridable via the
        ncollection_saas.portal_url_template config parameter ({db} is filled)."""
        template = self.env['ir.config_parameter'].sudo().get_param(
            'ncollection_saas.portal_url_template', 'http://{db}.localhost:8069')
        return template.format(db=db)

    def _mark_done(self, setup_url=None):
        """Success: job done, tenant ready + active, portal URL set, welcome
        email queued (P2-T02 point 3)."""
        db = self.database_name
        self.write({'status': 'done', 'completed_at': fields.Datetime.now()})
        tenant = self.tenant_id
        # sudo(): authorises the guarded 'ready' transition (#228, not RPC-spoofable).
        tenant.sudo().write({
            'database_status': 'ready',
            'portal_url': tenant.portal_url or self._portal_url(db),
        })
        if tenant.onboarding_stage == 'signup':
            tenant.onboarding_stage = 'setup'
        # Align the tenant lifecycle with its now-active subscription (guarded:
        # only the trial->active transition; leave any other state untouched).
        if tenant.status == 'trial':
            tenant.action_activate()
        tenant._send_welcome_email(setup_url)
        # Track the tenant's platform subdomain + wildcard-cert expiry (P2-T06).
        # Best-effort: a hiccup here must not fail a good provision — the weekly
        # reconciliation cron (_cron_scan_ssl_expiry) backfills any record missed.
        try:
            self.env['ncollection.domain']._sync_for_tenant(tenant)
        except Exception:  # pylint: disable=broad-except
            _logger.warning(
                "Domain record sync failed for tenant '%s' "
                "(will self-heal via the weekly cron).", db, exc_info=True)
        self._append_log(self.env._("DONE — tenant database is login-ready."))

    def _mark_failed(self):
        self.write({'status': 'failed', 'completed_at': fields.Datetime.now()})
        self.tenant_id.write({'database_status': 'error'})
