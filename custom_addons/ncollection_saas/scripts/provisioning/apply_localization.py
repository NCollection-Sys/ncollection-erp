# -*- coding: utf-8 -*-
# pylint: disable=print-used
# (stdout IS the transport back to the platform process — see seed_tenant.py.)
"""Apply a country localization to an EXISTING tenant database (#469).

Runs inside an `odoo shell` subprocess against the tenant database (never a
cross-DB ORM call from the platform — Rule 3). Reads:

    NC_LOC_CHART     chart template code to end up on (e.g. 'ae')
    NC_LOC_CURRENCY  ISO code the company currency must be (e.g. 'AED')
    NC_LOC_FORCE     '1' to proceed even though accounting data exists

THE GUARD IS THE POINT. Odoo's `account.chart.template._load` deletes the
company's existing accounts, taxes, journals and moves before loading a new
chart — unless `_existing_accounting()` is true, in which case it loads the new
chart ON TOP of the old one instead. Either branch is destructive to a tenant
that has been trading: the first erases their books, the second leaves them
with two overlapping charts. So a database holding accounting data is REFUSED
here and the decision is escalated to a human, rather than being made by a
button whose label says "apply localization".

"Accounting data" means posted or drafted moves, not the empty chart a fresh
`account` install leaves behind — otherwise no tenant could ever be localized,
since installing `account` always creates accounts.

Idempotent: a company already on the target chart exits successfully having
changed nothing, so a retry after a timeout cannot double-load a chart or
duplicate taxes.
"""
import os

chart = (os.environ.get('NC_LOC_CHART') or '').strip()
currency = (os.environ.get('NC_LOC_CURRENCY') or '').strip()
force = (os.environ.get('NC_LOC_FORCE') or '').strip() == '1'

if not chart:
    raise AssertionError('apply localization: NC_LOC_CHART is required')

company = env['res.company'].search([], order='id', limit=1)  # noqa: F821
if not company:
    raise AssertionError('apply localization: the tenant has no company')

# --- idempotency: already there, nothing to do ---------------------------
if company.chart_template == chart:
    print('LOCALIZATION_APPLIED=already %s/%s' % (
        company.chart_template, company.currency_id.name))
else:
    # --- the guard --------------------------------------------------------
    move_count = env['account.move'].search_count([])  # noqa: F821
    if move_count and not force:
        raise AssertionError(
            'REFUSED: this tenant has %d accounting entr%s. Loading a chart of '
            'accounts over them would either delete their books or leave two '
            'overlapping charts. Localize at provisioning, or migrate this '
            'tenant deliberately with a human decision and a backup.'
            % (move_count, 'y' if move_count == 1 else 'ies'))

    Chart = env['account.chart.template']  # noqa: F821
    Chart.try_loading(chart, company, install_demo=False)
    # Odoo core's `account` install can leave a deferred generic_coa fallback
    # scheduled; cancel it so it cannot replace what we just loaded. Same
    # reasoning as ncollection_account_localization_uae's own hook.
    if hasattr(env.registry, '_auto_install_template'):  # noqa: F821
        del env.registry._auto_install_template  # noqa: F821
    # Let the country module finish its own setup (bilingual invoices, GCC
    # currencies, peg rates, FTA checklist) through ITS entry point rather than
    # duplicating any of it here.
    if hasattr(company, '_nc_apply_uae_localization'):
        company._nc_apply_uae_localization()
    env.cr.commit()  # noqa: F821
    company = company.browse(company.id)
    print('LOCALIZATION_APPLIED=%s/%s' % (
        company.chart_template, company.currency_id.name))

# --- verify the result, always -------------------------------------------
problems = []
if company.chart_template != chart:
    problems.append('chart_template is %r, expected %r'
                    % (company.chart_template, chart))
if currency and company.currency_id.name != currency:
    problems.append('currency is %r, expected %r'
                    % (company.currency_id.name, currency))
if problems:
    raise AssertionError('localization did not take: %s' % '; '.join(problems))
