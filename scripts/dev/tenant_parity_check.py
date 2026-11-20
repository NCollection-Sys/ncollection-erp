# -*- coding: utf-8 -*-
# pylint: disable=print-used
"""Read-only SaaS feature-parity check: does every tenant really have what it is entitled to?

Run INSIDE the platform's odoo container, against the PLATFORM database, through `odoo shell` (stdin):

    docker exec -i <odoo container> odoo shell -c <conf> -d <platform db> --no-http --log-level=error \
        < scripts/dev/tenant_parity_check.py

(`scripts/dev/tenant_parity_check.sh` does exactly that for one platform; `release_sync_check.sh` calls it.)

WHY. A release used to count as verified when the commits and the manifests matched. They did, on both platforms,
while every tenant provisioned after the journal operations release opened on Odoo's standard journal cards: the
module was never in its entitlement, so it was never installed (see ncollection_subscription/models/companions.py).
Commit equality says nothing about a module that is not installed.

WHAT IT REPORTS, per database, in the categories the release workflow distinguishes:

  SOURCE SYNC          not here (release_sync_check.sh: commit / tree).
  MODULE SYNC          installed version == code version; no pending install / upgrade / removal.
  FUNCTIONAL PARITY    every module of the tenant's entitlement (+ core, + dependency closure) installed; every
                       satisfied auto_install module installed; every view of an installed ncollection_* module
                       present and active.
  EXPECTED PLATFORM-ONLY DIFFERENCES   platform modules (ncollection_saas, ...) on the platform and NOT on a tenant:
                       one on a tenant is a FAILURE (Rule 3).
  EXPECTED SUBSCRIPTION DIFFERENCES    modules a tenant lacks because its plan / overrides exclude them: reported as
                       information, never as a failure, and never "fixed".

Read-only: tenant databases are opened through a plain cursor that only SELECTs, and nothing is written to the
platform. Prints one `PARITY_JSON=` line (machine-readable) and a final `PARITY_RESULT=PASS|FAIL`.
"""
import json

import odoo
from odoo.modules.module import get_manifest

PLATFORM_ONLY = ('ncollection_saas', 'ncollection_subscription', 'ncollection_reseller', 'ncollection_billing')
CORE = ('base', 'ncollection_core', 'ncollection_branding', 'ncollection_auth')

Module = env['ir.module.module'].sudo()  # noqa: F821
catalog = {m.name: m for m in Module.search([])}


def code_version(name):
    module = catalog.get(name)
    return module.installed_version if module else None  # the code's adapted manifest version


def closure(names):
    seen, stack = set(), list(names)
    while stack:
        name = stack.pop()
        if name in seen or name not in catalog:
            seen.add(name)
            continue
        seen.add(name)
        stack.extend(catalog[name].dependencies_id.mapped('name'))
    return seen


def inspect(cr, expected):
    """One database, through a SELECT-only cursor. `expected` is None for the platform database itself."""
    cr.execute("SELECT name, state, latest_version, auto_install FROM ir_module_module")
    rows = {name: (state, version, auto) for name, state, version, auto in cr.fetchall()}
    installed = {name for name, (state, _v, _a) in rows.items() if state == 'installed'}
    failures, info = [], []
    # MODULE SYNC
    for name in sorted(installed):
        code = code_version(name)
        if code and rows[name][1] != code:
            failures.append('MODULE SYNC: %s installed %s, code %s' % (name, rows[name][1], code))
    pending = sorted('%s(%s)' % (n, s) for n, (s, _v, _a) in rows.items()
                     if s in ('to install', 'to upgrade', 'to remove'))
    if pending:
        failures.append('MODULE SYNC: pending %s' % ', '.join(pending))
    # FUNCTIONAL PARITY
    if expected is not None:
        for name in sorted(closure(expected) - installed):
            failures.append('FUNCTIONAL PARITY: entitled module %s is %s' % (name, rows.get(name, ('absent',))[0]))
        for name in PLATFORM_ONLY:
            if name in installed:
                failures.append('PLATFORM-ONLY: %s is installed in a tenant' % name)
    cr.execute("""SELECT m.name, array_agg(d.name) FILTER (WHERE d.auto_install_required)
                    FROM ir_module_module m JOIN ir_module_module_dependency d ON d.module_id = m.id
                   WHERE m.auto_install AND m.state = 'uninstalled' GROUP BY m.name""")
    for name, triggers in cr.fetchall():
        manifest = get_manifest(name) or {}
        if not manifest.get('installable', True) or manifest.get('countries'):
            continue
        if triggers and set(triggers) <= installed:
            failures.append('FUNCTIONAL PARITY: auto_install %s not installed (triggers %s installed)'
                            % (name, ','.join(sorted(triggers))))
    cr.execute("""SELECT d.module, d.name, v.id IS NOT NULL, coalesce(v.active, false)
                    FROM ir_model_data d LEFT JOIN ir_ui_view v ON v.id = d.res_id
                   WHERE d.model = 'ir.ui.view' AND d.module LIKE 'ncollection\\_%'""")
    views = 0
    for module, xmlid, present, active in cr.fetchall():
        if module not in installed:
            continue
        if not present or not active:
            failures.append('FUNCTIONAL PARITY: view %s.%s %s'
                            % (module, xmlid, 'missing' if not present else 'inactive'))
        else:
            views += 1
    ours = sorted(n for n in installed if n.startswith('ncollection_'))
    if expected is not None:
        not_entitled = sorted(n for n in installed - closure(expected) if n.startswith('ncollection_'))
        if not_entitled:
            info.append('installed but not in the entitlement (auto_install or by hand): %s' % ', '.join(not_entitled))
    return {'installed': len(installed), 'ncollection': ours, 'views_ok': views, 'failures': failures, 'info': info}


report = {'platform': env.cr.dbname, 'databases': {}}  # noqa: F821
report['databases'][env.cr.dbname] = dict(inspect(env.cr, None), role='platform')  # noqa: F821

Tenant = env['ncollection.tenant'].sudo().with_context(active_test=False)  # noqa: F821
existing = set(odoo.service.db.list_dbs(True))
plan_modules = set()
for plan in env['ncollection.subscription.plan'].sudo().with_context(active_test=False).search([]):  # noqa: F821
    plan_modules |= set(plan.get_allowed_module_list())
for tenant in Tenant.search([('database_name', '!=', False)], order='database_name'):
    db = tenant.database_name
    entry = {'role': 'tenant', 'status': tenant.status, 'database_status': tenant.database_status,
             'plan': tenant.plan_id.code}
    if db not in existing:
        entry.update(failures=['database is missing'] if tenant.database_status == 'ready' else [],
                     info=['no database'])
        report['databases'][db] = entry
        continue
    expected = list(CORE) + tenant._nc_effective_module_list()
    with odoo.sql_db.db_connect(db).cursor() as tcr:
        tcr.execute("SET TRANSACTION READ ONLY")
        entry.update(inspect(tcr, expected))
        tcr.rollback()
    # EXPECTED SUBSCRIPTION DIFFERENCES: plan-selectable apps this tenant's entitlement does not name.
    entry['subscription_excluded'] = sorted(plan_modules - set(expected))
    entry['plan'] = tenant.plan_id.code
    entry['country'] = tenant.country_id.code
    report['databases'][db] = entry

# PEER PARITY. The entitlement itself can be wrong (that IS the journal-card defect: the modules were never in it, so
# a check against the entitlement alone passes). So also compare tenants on the SAME plan: an NCollection module that
# a same-plan peer has and this tenant lacks is a gap, unless it is country- or eligibility-specific (localization
# packages, gated modules), which legitimately differs between peers.
try:
    from odoo.addons.ncollection_subscription.models.localization import LOCALIZATION_PACKAGES
    from odoo.addons.ncollection_subscription.models.gated_modules import GATED_MODULES
    country_specific = {m for pkg in LOCALIZATION_PACKAGES.values() for m in pkg['modules']} | set(GATED_MODULES)
except ImportError:  # an older release: fall back to the naming convention
    country_specific = set()
tenants = {db: e for db, e in report['databases'].items() if e.get('role') == 'tenant' and 'ncollection' in e}
for db, entry in tenants.items():
    peers = [e for other, e in tenants.items() if other != db and e.get('plan') == entry.get('plan')]
    mine = set(entry['ncollection'])
    gaps = sorted(m for m in {m for e in peers for m in e['ncollection']} - mine - country_specific
                  if 'localization' not in m and 'einvoicing' not in m)
    if gaps:
        entry.setdefault('failures', []).append(
            'PEER PARITY: same-plan (%s) tenants have %s' % (entry.get('plan'), ', '.join(gaps)))

failed = {db: e['failures'] for db, e in report['databases'].items() if e.get('failures')}
for db, entry in report['databases'].items():
    state = 'FAIL' if entry.get('failures') else 'PASS'
    print('%-5s %-22s %-8s installed=%s ncollection=%s views_ok=%s' % (
        state, db, entry.get('role'), entry.get('installed', '-'), len(entry.get('ncollection', [])),
        entry.get('views_ok', '-')))
    for line in entry.get('failures', []):
        print('      FAIL  %s' % line)
    for line in entry.get('info', []):
        print('      info  %s' % line)
    if entry.get('subscription_excluded'):
        print('      info  EXPECTED SUBSCRIPTION DIFFERENCE (plan excludes): %s'
              % ', '.join(entry['subscription_excluded']))
print('PARITY_JSON=' + json.dumps(report, sort_keys=True))
print('PARITY_RESULT=' + ('FAIL' if failed else 'PASS'))
