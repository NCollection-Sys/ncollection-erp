# -*- coding: utf-8 -*-
{
    'name': 'NCollection Tenant Data Import Toolkit',
    'version': '19.0.1.0.0',
    'category': 'Extra Tools',
    'summary': 'Onboarding import toolkit: starter CSV templates + a guided '
               'wizard over Odoo\'s native importer, with admin-friendly '
               'row-level validation for a new tenant\'s opening data',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    # P3-T11 (DELIVERABLE_1_SYSTEM_DESIGN.md). REUSE-first (Rule 2/5): Odoo 19
    # Community already solves the hard problems — base_import (CSV/XLSX mapping,
    # dry-run "Test", external-ID create/update, per-row errors), the
    # account.account.opening_* self-balancing opening move, and
    # stock.quant.inventory_quantity self-balancing adjustments. This module adds
    # only the onboarding LAYER Odoo lacks: documented starter templates, a
    # sequenced wizard that opens each native importer in the right order, and a
    # dry-run validation step that rephrases base_import's errors for a
    # non-technical admin. No custom import engine, no OCA dependency
    # (base_import_async was surveyed and deferred: AGPL + would push queue_job
    # into every tenant DB — the onboarding case doesn't need it).
    #   - contacts : the Customers/Vendors app + res.partner list (import target).
    #   - account  : opening balances (account.account.opening_debit/credit).
    #   - stock    : opening stock (stock.quant physical-inventory import).
    #   - ncollection_account_core : the financial family base (family layering).
    # base_import ships with Odoo core (web) — no need to declare it.
    'depends': [
        'contacts', 'account', 'stock', 'ncollection_account_core',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/onboarding_views.xml',
    ],
    # The starter templates ship inside the module and are served for download
    # by the wizard (read from the module path at runtime); they are not ORM data.
}
