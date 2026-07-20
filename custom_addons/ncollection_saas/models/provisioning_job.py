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
# lowercase, starts with a letter, 3–63 chars — also a safe SQL identifier.
DB_NAME_RE = re.compile(r'^[a-z][a-z0-9_]{2,62}$')

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
        """Enqueue provisioning on the dedicated queue channel (off HTTP workers)."""
        for job in self:
            job._check_quota()
            job.with_delay(
                channel=PROVISION_CHANNEL,
                description=self.env._("Provision tenant DB '%s'", job.database_name),
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
        Idempotent-safe: a failed run leaves no partial DB behind.
        """
        self.ensure_one()
        db = self.database_name
        self._set_status('running', self.env._("Provisioning started for database '%s'.", db))
        self.tenant_id.database_status = 'provisioning'
        try:
            self._validate_db_name(db)
            self._append_log(self.env._("Name validated."))

            modules = self._module_list()
            self._run_odoo_init(db, modules)
            self._append_log(
                self.env._("Database created; modules installed: %s", ', '.join(modules)))

            self._seed_tenant(db)
            self._append_log(
                self.env._("Tenant seeded: admin (forced reset), workspace config, branding."))

            self._mark_done()
        except Exception as exc:  # noqa: BLE001 - engine must catch to roll back
            self._append_log(self.env._("FAILED: %s", exc))
            self._rollback(db)
            self._mark_failed()
            _logger.exception("Provisioning failed for database %s", db)
            raise
        return True

    # ---- steps -----------------------------------------------------------

    def _validate_db_name(self, db):
        """Reject injection / overwrite / reserved names before any side effect."""
        if not db or not DB_NAME_RE.match(db):
            raise ValidationError(self.env._(
                "Invalid database name '%s': must match ^[a-z][a-z0-9_]{2,62}$.", db))
        if db in RESERVED_DB_NAMES:
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
        """
        tenant = self.tenant_id
        plan = tenant.plan_id
        sub = tenant.subscription_id
        with open(SEED_SCRIPT, encoding='utf-8') as fh:
            script = fh.read()
        env_vars = os.environ.copy()
        env_vars.update({
            'NC_COMPANY': tenant.company_name or 'Tenant',
            'NC_ADMIN_EMAIL': tenant.email or '',
            'NC_ALLOWED_MODULES': plan.allowed_module_names if plan else '',
            'NC_PLAN_CODE': plan.code if plan else '',
            'NC_MAX_USERS': str(plan.max_users if plan else 1),
            'NC_SUB_STATUS': (sub.status if sub else 'active') or 'active',
        })
        # `shell` MUST be the first argument (odoo <subcommand> <options>).
        cmd = ['odoo', 'shell'] + self._odoo_conn_args(db) + ['--log-level=error']
        self._run_subprocess(cmd, self.env._("tenant seed"), stdin=script, env=env_vars)

    def _rollback(self, db):
        """Drop a half-provisioned DB so no zombie is left (SECURITY §11)."""
        try:
            if DB_NAME_RE.match(db or '') and self._database_exists(db):
                self._drop_database(db)
                self._append_log(self.env._("Rolled back: dropped database '%s'.", db))
        except Exception as exc:  # noqa: BLE001
            self._append_log(self.env._(
                "Rollback WARNING: could not drop %(db)s: %(err)s", db=db, err=exc))
            _logger.exception("Rollback failed for %s", db)

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

    def _mark_done(self):
        self.write({'status': 'done', 'completed_at': fields.Datetime.now()})
        self.tenant_id.write({'database_status': 'ready'})
        if self.tenant_id.onboarding_stage == 'signup':
            self.tenant_id.onboarding_stage = 'setup'
        self._append_log(self.env._("DONE — tenant database is login-ready."))

    def _mark_failed(self):
        self.write({'status': 'failed', 'completed_at': fields.Datetime.now()})
        self.tenant_id.write({'database_status': 'error'})
