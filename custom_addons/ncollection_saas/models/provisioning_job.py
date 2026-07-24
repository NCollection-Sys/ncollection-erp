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
import subprocess
from datetime import timedelta

import psycopg2
from psycopg2 import sql

from odoo import fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import config

_logger = logging.getLogger(__name__)

# Forbidden tenant DB / subdomain names (ARCHITECTURE_DATA_PLATFORM §2).
RESERVED_DB_NAMES = frozenset(
    {'admin', 'www', 'staging', 'api', 'postgres', 'template0', 'template1'}
)
# lowercase alphanumeric, starts with a letter, 3–63 chars — a safe SQL identifier
# AND a valid subdomain label. NO underscore: tenant key === subdomain === database
# name, and underscores are invalid in hostnames, so an underscore name is unroutable
# under db_filter=^%d$ (#211). Matches checkout.SUBDOMAIN_RE / tenant._GENERATED_NAME_RE.
DB_NAME_RE = re.compile(r'^[a-z][a-z0-9]{2,62}$')

# Always installed into a tenant DB (base + license/config + branding).
CORE_TENANT_MODULES = ('base', 'ncollection_core', 'ncollection_branding')

QUOTA_PARAM = 'ncollection_saas.provisioning_quota_per_hour'
DEFAULT_QUOTA = 20
PROVISION_CHANNEL = 'root.provisioning'
SEED_SCRIPT = os.path.join(
    os.path.dirname(__file__), '..', 'scripts', 'provisioning', 'seed_tenant.py'
)
SUBPROCESS_TIMEOUT = 1800  # 30 min hard cap per odoo subprocess


class ProvisioningJob(models.Model):
    _inherit = 'ncollection.provisioning.job'

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
        self.tenant_id.database_status = 'provisioning'
        created = False
        try:
            if retry and DB_NAME_RE.match(db or '') and self._database_exists(db):
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
        """Reject injection / overwrite / reserved names before any side effect."""
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
        self._run_subprocess(cmd, self.env._("database init"))

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
        # `shell` MUST be the first argument (odoo <subcommand> <options>).
        cmd = ['odoo', 'shell'] + self._odoo_conn_args(db) + ['--log-level=error']
        out = self._run_subprocess(
            cmd, self.env._("tenant seed"), stdin=script, env=env_vars)
        return self._parse_setup_url(out)

    @staticmethod
    def _parse_setup_url(stdout):
        """Extract the SEED_SETUP_URL=<url> line the seed script prints, if any."""
        for line in (stdout or '').splitlines():
            if line.startswith('SEED_SETUP_URL='):
                return line[len('SEED_SETUP_URL='):].strip() or None
        return None

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
            if DB_NAME_RE.match(db or '') and self._database_exists(db):
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

    def _odoo_conn_args(self, db):
        """Config + DB connection flags shared by the init and shell subprocesses.

        The rcfile carries addons_path; the DB host/user/password are injected
        by the docker entrypoint as env vars for the MAIN process only, so a
        spawned `odoo` would otherwise fall back to a local socket. We pass them
        explicitly from the running config. Callers prepend `odoo` (+ optional
        `shell` subcommand) and append their own options.
        """
        args = []
        if config.rcfile:
            args += ['-c', config.rcfile]
        args += ['-d', db]
        for flag, key, default in (
            ('--db_host', 'db_host', 'db'),
            ('--db_port', 'db_port', '5432'),
            ('--db_user', 'db_user', 'odoo'),
            ('--db_password', 'db_password', 'odoo'),
        ):
            value = config[key] or default
            args.append('%s=%s' % (flag, value))
        return args

    def _run_subprocess(self, cmd, label, stdin=None, env=None):
        result = subprocess.run(
            cmd, input=stdin, env=env, capture_output=True, text=True,
            timeout=SUBPROCESS_TIMEOUT, check=False,
        )
        if result.returncode != 0:
            tail = (result.stderr or result.stdout or '')[-2000:]
            raise UserError(self.env._(
                "Step %(label)s failed (exit %(code)s):\n%(out)s",
                label=label, code=result.returncode, out=tail))
        return result.stdout or ''

    def _db_conn_params(self, dbname):
        params = {
            'dbname': dbname,
            'host': config['db_host'] or 'db',
            'port': config['db_port'] or 5432,
            'user': config['db_user'] or 'odoo',
            'password': config['db_password'] or 'odoo',
        }
        return {k: v for k, v in params.items() if v not in (False, None, '')}

    def _database_exists(self, db):
        conn = psycopg2.connect(**self._db_conn_params('postgres'))
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db,))
            return cur.fetchone() is not None
        finally:
            conn.close()

    def _drop_database(self, db):
        conn = psycopg2.connect(**self._db_conn_params('postgres'))
        conn.autocommit = True
        try:
            conn.cursor().execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(db)))
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
        tenant.write({
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
