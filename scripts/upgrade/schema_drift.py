# -*- coding: utf-8 -*-
"""Run INSIDE ``odoo shell -d <db>`` (script on stdin). Reports whether the database is behind the code on disk.

It prints machine-readable markers and exits through the caller, because ``odoo shell`` always exits 0:

    SCHEMA_DRIFT=<0|1>
    SCHEMA_DRIFT_MODULES=<comma separated modules whose code is newer than the database>
    SCHEMA_DRIFT_COLUMNS=<stored fields with no column>   SCHEMA_DRIFT_TABLES=<models with no table>

It changes nothing. NOTE it still LOADS the registry, and loading a registry runs the modules' ``_register_hook``s
(some of which write: e.g. the audit rules are re-seeded on every load). So point it at a scratch database or a copy,
never at a database that must stay untouched; ``scripts/upgrade/rehearse_upgrade.sh`` does exactly that.
"""
from odoo.addons.ncollection_core.models.schema_drift import describe, drift_report

report = drift_report(env)  # noqa: F821 - ``env`` is provided by ``odoo shell``
print('SCHEMA_DRIFT_DB=%s' % report['database'])
print('SCHEMA_DRIFT=%d' % (1 if report['drift'] else 0))
print('SCHEMA_DRIFT_MODULES=%s' % ','.join(name for name, _installed, _disk in report['behind']))
print('SCHEMA_DRIFT_COLUMNS=%d' % len(report['missing_columns']))
print('SCHEMA_DRIFT_TABLES=%d' % len(report['missing_tables']))
for line in describe(report):
    print('SCHEMA_DRIFT_ITEM=%s' % line)
