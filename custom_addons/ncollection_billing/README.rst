==================
NCollection Billing
==================

Subscription billing engine (P2-T11) for the NCollection ERP platform.

Generates exactly one ``account.move`` customer invoice on subscription
purchase (activation) and each renewal, applies UAE VAT 5%, prorates
mid-cycle upgrades, links each invoice to its tenant and subscription, and
tracks payment status back onto the subscription.

The billing engine runs in the **admin/platform database** and uses Odoo's
own accounting engine (``account.move`` / ``account.tax``) per
``FINANCIAL_PLATFORM_ARCHITECTURE.md`` §4/§5: Odoo owns the accounting engine,
NCollection owns the billing workflow. A post-install hook provisions the
accounting prerequisites (generic chart of accounts, 5% VAT tax, billing
product) idempotently.

This is the alternative (dedicated-module) implementation of P2-T11 offered
for comparison against the in-``ncollection_saas`` approach.
