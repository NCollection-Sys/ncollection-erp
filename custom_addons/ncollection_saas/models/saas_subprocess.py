# -*- coding: utf-8 -*-
"""Shared platform-side subprocess / maintenance-DB primitives (P3-T14).

The platform NEVER opens an ORM cursor on a tenant DB (Rule 3 / two-layer,
ARCHITECTURE_DATA_PLATFORM §10): per-tenant work runs in ISOLATED ``odoo``
subprocesses, and destructive DDL goes through a direct psycopg2 maintenance
connection. Both the provisioning engine (P2-T01) and the fleet migration
orchestrator (P3-T14) need the same primitives; this abstract mixin is their
shared, canonical home.

``provisioning_job.py`` originally kept its own copies — P3-T14 deliberately did
not touch shipped, isolation-critical provisioning code — and a guard-test policed
the two sets for drift. #243 removed that duplication: provisioning now inherits
this mixin, so the definitions below are the ONLY ones.

The one thing NOT unified is the database-name existence rule. See
``_assert_safe_db_name`` below and ``provisioning_job._validate_db_name``: they
encode opposite requirements (upgrade an existing DB vs. create a new one), so
merging them would silently break one caller.
"""
import logging
import re
import subprocess

import psycopg2
from psycopg2 import sql

from odoo import models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import config

_logger = logging.getLogger(__name__)

# A safe SQL identifier AND a valid subdomain label. THE definition since #243 —
# provisioning imports these rather than keeping a second copy, so the injection
# allowlist cannot drift. Do not re-declare them elsewhere.
DB_NAME_RE = re.compile(r'^[a-z][a-z0-9]{2,62}$')
RESERVED_DB_NAMES = frozenset(
    {'admin', 'www', 'staging', 'api', 'postgres', 'template0', 'template1'})
SUBPROCESS_TIMEOUT = 1800  # 30 min hard cap per odoo subprocess


class SaasSubprocessMixin(models.AbstractModel):
    _name = 'ncollection.saas.subprocess.mixin'
    _description = 'Platform-side isolated odoo subprocess + maintenance-DB helpers'

    def _assert_safe_db_name(self, db):
        """Reject anything that is not a known-safe tenant DB identifier before it
        reaches a subprocess arg or a DROP — injection / self-target guard.

        Deliberately does NOT check existence: callers of this guard (fleet
        migration) operate on databases that must already exist. Provisioning's
        ``_validate_db_name`` adds that check because it CREATES — see the note
        there (#243).
        """
        if not db or not DB_NAME_RE.match(db):
            raise ValidationError(self.env._(
                "Unsafe database name '%s' (must match ^[a-z][a-z0-9]{2,62}$).", db))
        if db in RESERVED_DB_NAMES or db == self.env.cr.dbname:
            raise ValidationError(self.env._(
                "Refusing to operate on reserved/platform database '%s'.", db))

    def _odoo_conn_args(self, db):
        """`-c rcfile -d db --db_*` flags for a spawned odoo subprocess. The
        docker entrypoint sets DB creds as env for the MAIN process only, so a
        child odoo would otherwise fall back to a local socket — pass explicitly."""
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
            args.append('%s=%s' % (flag, config[key] or default))
        return args

    def _run_odoo_subprocess(self, cmd, label, stdin=None, env=None,
                             timeout=SUBPROCESS_TIMEOUT):
        """Run a spawned odoo command; raise UserError with the log tail on a
        non-zero exit. Never shell=True — ``cmd`` is always an argv list."""
        result = subprocess.run(  # noqa: S603 - argv list, never shell=True
            cmd, input=stdin, env=env, capture_output=True, text=True,
            timeout=timeout, check=False)
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

    def _drop_database(self, db):
        """DROP DATABASE (FORCE) via a maintenance connection. The caller MUST
        have passed the name through _assert_safe_db_name first."""
        conn = psycopg2.connect(**self._db_conn_params('postgres'))
        conn.autocommit = True
        try:
            conn.cursor().execute(sql.SQL(
                "DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(db)))
        finally:
            conn.close()
