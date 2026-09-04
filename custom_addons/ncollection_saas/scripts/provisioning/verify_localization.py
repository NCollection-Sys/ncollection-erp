# -*- coding: utf-8 -*-
# pylint: disable=print-used
# (stdout IS the transport back to the platform process — see seed_tenant.py.)
"""Assert a tenant database is ACTUALLY localized (#469).

Runs inside an `odoo shell` subprocess against the tenant database (never a
cross-DB ORM call from the platform — Rule 3), reading its expectations from
the environment:

    NC_LOC_CHART     chart template code the company must be on (e.g. 'ae')
    NC_LOC_CURRENCY  ISO code the company currency must be (e.g. 'AED')

WHY THIS EXISTS. "l10n_ae is installed" is not evidence of localization, and
believing it is how a UAE product shipped tenants on `generic_coa` in USD with
placeholder 15% taxes while every module reported success. `_nc_apply_uae_-
localization` is deliberately fail-soft — it must never break an install — so
the failure it is designed to survive is exactly the failure nothing else
notices. This is the thing that notices.

Prints one `LOCALIZATION_OK=...` line on success, or raises. The caller treats
a non-zero exit as a provisioning failure, which rolls the database back and
leaves the job retryable.
"""
import os

chart = (os.environ.get('NC_LOC_CHART') or '').strip()
currency = (os.environ.get('NC_LOC_CURRENCY') or '').strip()

company = env['res.company'].search([], order='id', limit=1)  # noqa: F821
if not company:
    raise AssertionError('localization check: the tenant has no company')

problems = []
if chart and company.chart_template != chart:
    problems.append(
        "chart_template is %r, expected %r" % (company.chart_template, chart))
if currency and company.currency_id.name != currency:
    problems.append(
        "currency is %r, expected %r" % (company.currency_id.name, currency))

# A chart that loaded but produced no taxes is not a usable localization: the
# company would have a legal-looking chart and no way to charge VAT. Checked
# separately from the chart code because the two fail independently — a
# partially-rolled-back load leaves the code set and the taxes missing.
tax_count = env['account.tax'].search_count(  # noqa: F821
    [('company_id', '=', company.id)])
if chart and not tax_count:
    problems.append('the chart loaded but the company has no taxes')

if problems:
    raise AssertionError(
        'localization check FAILED for company %r: %s'
        % (company.name, '; '.join(problems)))

print('LOCALIZATION_OK=%s/%s/%s taxes' % (
    company.chart_template, company.currency_id.name, tax_count))
