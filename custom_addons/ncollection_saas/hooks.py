# -*- coding: utf-8 -*-
"""Admin-DB billing bootstrap (P2-T11).

Billing invoices tenants for their SaaS subscriptions using Odoo's `account`
module, in the PLATFORM (admin) DB — never a tenant DB. `account.move` needs a
chart of accounts, a sale journal, a VAT tax and an income account on the
platform company; a bare `account` install ships none of a chart. This module
loads the UAE chart (l10n_ae — AED + UAE CoA + 5% VAT) onto the platform
company, then ensures the single 5% sale VAT tax and the "Subscription" service
product the billing engine lines its invoices with.

WHY the load is deferred, not done outright in `post_init_hook`:
`account.chart.template.try_loading` resolves l10n_ae's accounts/taxes through
`@template` methods that need a FULLY-LOADED registry. During a module's
`post_init_hook` the registry is only partially loaded, so the load silently
no-ops (chart stays generic_coa/USD). We therefore expose an idempotent
`ensure_billing_setup(env)` that `post_init_hook` calls best-effort AND the
billing engine calls at the top of its first invoice run — by then the registry
is fully loaded (normal request/queue-job context), so the chart load takes.
Every step is guarded, so the second and later calls are cheap no-ops.

Idempotent + self-healing. Everything here is the platform's OWN admin-DB
accounting data (DELIVERABLE_1 §2.5 places billing in the admin DB).
"""

import logging

_logger = logging.getLogger(__name__)

VAT_PARAM = 'ncollection_saas.vat_tax_id'
PRODUCT_PARAM = 'ncollection_saas.subscription_product_id'


def _ensure_chart(env, company):
    """Ensure the UAE chart (l10n_ae — AED + UAE CoA + 5% VAT) on the platform company.

    `account`'s own post_init auto-loads a chart keyed on the company's country;
    with the platform company's country unset it falls back to generic_coa/USD.
    We want the UAE chart, so unless the company is already on 'ae' we set its
    country to the UAE and load 'ae'. `try_loading` needs a fully-loaded registry
    (see the module docstring) and refuses once real accounting entries exist, so
    this only ever switches a still-empty company from a fully-loaded context — a
    no-op on upgrade (chart_template is already 'ae').
    """
    if company.chart_template == 'ae':
        return  # already on the UAE chart
    ae_country = env.ref('base.ae', raise_if_not_found=False)
    if ae_country and company.country_id != ae_country:
        company.country_id = ae_country
    _logger.info("Loading the UAE chart of accounts onto company %s (P2-T11 billing)", company.name)
    env['account.chart.template'].try_loading('ae', company, install_demo=False)


def _ensure_vat_tax(env, company):
    """Return the platform company's 5% output VAT tax (from l10n_ae, or create)."""
    tax = env['account.tax'].search([
        ('company_id', '=', company.id),
        ('type_tax_use', '=', 'sale'),
        ('amount_type', '=', 'percent'),
        ('amount', '=', 5.0),
    ], limit=1)
    if not tax:
        tax = env['account.tax'].create({
            'name': 'VAT 5%',
            'amount': 5.0,
            'amount_type': 'percent',
            'type_tax_use': 'sale',
            'company_id': company.id,
        })
    env['ir.config_parameter'].sudo().set_param(VAT_PARAM, str(tax.id))
    return tax


def _ensure_product(env, company, tax):
    """Ensure the 'NCollection Subscription' service product (invoice line)."""
    product = env.ref('ncollection_saas.product_subscription', raise_if_not_found=False)
    if not product:
        product = env['product.product'].create({
            'name': 'NCollection Subscription',
            'type': 'service',
            'sale_ok': True,
            'purchase_ok': False,
            'taxes_id': [(6, 0, tax.ids)],
        })
        env['ir.model.data'].create({
            'name': 'product_subscription',
            'module': 'ncollection_saas',
            'model': 'product.product',
            'res_id': product.id,
            'noupdate': True,
        })
    elif tax not in product.taxes_id:
        product.taxes_id = [(6, 0, tax.ids)]
    env['ir.config_parameter'].sudo().set_param(PRODUCT_PARAM, str(product.id))
    return product


def ensure_billing_setup(env):
    """Idempotently ensure the platform company can invoice: chart, VAT, product.

    Safe to call repeatedly. `post_init_hook` calls it best-effort at install and
    the billing engine calls it before the first invoice, where a fully-loaded
    registry lets the chart load actually take (see the module docstring).
    """
    company = env.company
    _ensure_chart(env, company)
    tax = _ensure_vat_tax(env, company)
    product = _ensure_product(env, company, tax)
    return tax, product


def post_init_hook(env):
    ensure_billing_setup(env)
    _logger.info("P2-T11 billing bootstrap complete for company %s", env.company.name)
