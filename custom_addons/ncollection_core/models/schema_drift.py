# -*- coding: utf-8 -*-
"""Code-versus-database drift: the reason an HTTP request can answer 500 after a perfectly valid code change.

THE FAILURE THIS NAMES (REGRESSIONS.md R-012b / R-017 / R-019, and the 2026-10 incident below)
-----------------------------------------------------------------------------------------------
Odoo builds a model's SELECT from the fields its *code* declares. It changes the *schema* only on a module upgrade
(``-u``). It never compares the version recorded in the database with the version on disk. So the moment a checkout,
a merge or a deploy puts newer code under a database that has not been upgraded:

    code declares ``res.users.nc_must_change_password``  ->  every SELECT on res_users names that column
    the database has no such column                      ->  psycopg2.errors.UndefinedColumn
    the first request that reads the model               ->  HTTP 500  (``GET /web/login``, ``POST /web/login``)

Nothing warns beforehand: the registry loads, ``/web/health`` answers 200, and the first real page 500s. In this
repository's shared dev stack the code is one bind-mounted tree served to EVERY database at once, so one ``git
checkout`` can leave a dozen databases in this state together. The incident that prompted this module reproduced
both ways: a stale tenant failed ``GET /web/login`` on ``res_users.nc_must_change_password`` (installed core 1.19.0,
code 1.22.0) and the real ``ncollection`` data failed ``POST /web/login`` on ``res_partner.nc_trn`` (code from a
branch that added the column, data never upgraded).

WHAT THIS DOES
--------------
It makes the state *visible* and *machine-checkable*; it never repairs anything and never suppresses a traceback.

* :func:`version_drift` - installed modules whose version on disk is NEWER than the version recorded in the database.
* :func:`schema_gaps`   - stored fields (and models) the registry declares that have no column (or table). This is the
  condition that actually produces the 500, and it also catches a gap at an UNCHANGED version (a column lost, an
  upgrade that half-ran).
* :func:`drift_report`  - both, as one dict.
* ``ir.module.module._register_hook`` logs one ERROR when the report is not empty, at the moment the registry loads,
  naming the modules and the repair, so the first line in the log is the cause and not a column error 40 lines into
  a stack trace. It never raises: a diagnostic must not be able to break the registry it is diagnosing.

The same functions back ``scripts/upgrade/schema_drift.py`` (any database, read-only), ``make schema-drift`` and the
upgrade rehearsal gate (``scripts/upgrade/rehearse_upgrade.sh``).
"""
import logging

from odoo import api, models
from odoo.modules.module import Manifest
from odoo.tools import parse_version

_logger = logging.getLogger(__name__)

# How many offenders one log line names. The full report is available from the functions above.
_LOG_LIMIT = 12


def _disk_version(name):
    manifest = Manifest.for_addon(name, display_warning=False)
    return manifest.version if manifest else None


def version_drift(env):
    """Installed modules whose code on disk is newer than the database: ``[(module, installed, on_disk)]``."""
    env.cr.execute("SELECT name, latest_version FROM ir_module_module WHERE state = 'installed' ORDER BY name")
    behind = []
    for name, installed in env.cr.fetchall():
        on_disk = _disk_version(name)
        if not on_disk or not installed:
            continue
        if parse_version(on_disk) > parse_version(installed):
            behind.append((name, installed, on_disk))
    return behind


def schema_gaps(env):
    """What the registry declares but the database lacks: ``{'tables': [model], 'columns': [(model, field)]}``.

    Only models Odoo manages itself (``_auto``) and only stored fields with a real column type are considered, so SQL
    views, abstract models and one2many / many2many fields cannot be false positives.
    """
    env.cr.execute(
        "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = current_schema()"
    )
    columns = {}
    for table, column in env.cr.fetchall():
        columns.setdefault(table, set()).add(column)
    tables, missing = [], []
    for name, model in sorted(env.registry.models.items()):
        if not model._auto or model._abstract or not model._table:
            continue
        have = columns.get(model._table)
        if have is None:
            tables.append(name)
            continue
        for fname, field in sorted(model._fields.items()):
            if field.store and field.column_type and fname not in have:
                missing.append((name, fname))
    return {'tables': tables, 'columns': missing}


def drift_report(env):
    """Both checks as one dict; ``bool(report['drift'])`` is the single answer: is this database behind its code."""
    behind = version_drift(env)
    gaps = schema_gaps(env)
    return {
        'database': env.cr.dbname,
        'behind': behind,
        'missing_tables': gaps['tables'],
        'missing_columns': gaps['columns'],
        'drift': bool(behind or gaps['tables'] or gaps['columns']),
    }


def describe(report):
    """The report as the lines a human (or a log) reads."""
    lines = []
    for name, installed, on_disk in report['behind']:
        lines.append('module %s: database %s < code %s' % (name, installed, on_disk))
    for model in report['missing_tables']:
        lines.append('model %s: table missing' % model)
    for model, fname in report['missing_columns']:
        lines.append('field %s.%s: column missing' % (model, fname))
    return lines


class IrModuleModule(models.Model):
    _inherit = 'ir.module.module'

    @api.model
    def _register_hook(self):
        result = super()._register_hook()
        try:
            # A savepoint: catching the Python exception is not enough, a failed statement leaves the transaction
            # ABORTED and the registry load that follows (other hooks, flush_all) would then fail on it. No flush: the
            # report only reads.
            with self.env.cr.savepoint(flush=False):
                report = drift_report(self.env)
            # The release gate refuses traffic to a database whose schema lacks what the code declares (for as long
            # as its module rows stay as they are now: the cursor reads that token).
            from .. import release_gate
            release_gate.note_schema_report(self.env.cr.dbname, report, self.env.cr)
        except Exception:  # noqa: BLE001 - a diagnostic must never be able to break the registry it diagnoses
            _logger.warning("Schema drift check could not run.", exc_info=True)
            return result
        if report['drift']:
            lines = describe(report)
            _logger.error(
                "SCHEMA DRIFT in database %r: the code on disk is ahead of this database (%d module(s) behind, "
                "%d table(s) and %d column(s) missing). Any request that reads an affected model will fail with "
                "HTTP 500 (UndefinedColumn / UndefinedTable) until ONLY the affected modules are upgraded, or code "
                "matching this database is served. %s%s",
                report['database'], len(report['behind']), len(report['missing_tables']),
                len(report['missing_columns']), '; '.join(lines[:_LOG_LIMIT]),
                '; ...' if len(lines) > _LOG_LIMIT else '',
            )
        return result
