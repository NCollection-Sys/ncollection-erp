=======================
NCollection Account Core
=======================

**Task:** F1-T01, F1-T02 · **Architecture:** ``FINANCIAL_PLATFORM_ARCHITECTURE.md`` §4/§6/§7

The thin base layer between Odoo's ``account`` engine and the rest of the
NCollection financial platform (``ncollection_account_reports``, ``_dashboard``,
``_analytics``, ``_approval``, ``_budget``, ``_assets``, ``_documents``,
``_audit``, ``_localization_uae``). It provides **shared glue only** and never
duplicates accounting-engine logic — Odoo owns posting, journals and taxes.

What it owns
============

- Shared financial **mixins** (``ncollection.account.mixin``)
- SaaS **configuration surface** and business **validation-rule** scaffolding
  (see *Deferred*, below)
- Subscription **feature-restriction hooks**

What it must NEVER own
======================

Reports · Dashboards · Budget logic · Analytics · Audit · Assets — each of those
is a separate ``ncollection_account_*`` module (FPA §7 / §8 placement rules). If
a feature does not clearly belong here, **stop and review the architecture** —
do not park it in core for convenience.

``ncollection.account.mixin``
=============================

The extension point every downstream ``ncollection_account_*`` model mixes in::

    class AccountMove(models.Model):
        _inherit = ['account.move', 'ncollection.account.mixin']

``_nc_feature_enabled(module_name) -> bool``
    Is ``module_name`` (a plan-priced application namespace, e.g. ``'account'``,
    ``'sale'``) licensed by the tenant's current plan?

``_nc_require_feature(module_name)``
    Raise a branded upsell ``AccessError`` when it is not; return silently when
    it is. For imperative call sites (wizard/server actions, buttons).

Both are ``@api.model``, so they can also be called without inheriting:
``env['ncollection.account.mixin']._nc_feature_enabled('account')``.

Pass a plan-priced **application** namespace (``'account'``, ``'sale'`` …), never
an infrastructure namespace (``'base'``, ``'ir'``, ``'res'``, ``'mail'``,
``'web'`` or an ``'ncollection*'`` module) — Ring-2 never gates those, so the
helper is not meant to be asked about them. A missing/blank ``module_name``
raises ``ValueError`` (fail loud, not a silent "not licensed").

Integration with license enforcement (#11) — read, don't reimplement
--------------------------------------------------------------------

The gate reads the **same source of truth** as the platform's Ring-2 ORM license
enforcement (``ncollection_core``): the per-tenant ``ncollection.workspace.config``
singleton, via ``get_allowed_module_list()``. It is **not** a second enforcement
path — Ring-2 already denies CRUD on unlicensed namespaces at ``_check_access``.
These helpers are an opt-in "upsell early" convenience that fails fast and
legibly *before* the ORM would, and can never widen access beyond what Ring-2
permits.

Like Ring-2 it **exempts system/sudo callers** and is **fail-open** (no
workspace config, or an empty allowed list, reads as enabled) — the worst
regression is "not gated", never a bricked workspace.

Deferred (acknowledged, not gold-plated)
========================================

FPA §7 also lists **"SaaS Configuration / Configuration UI"** as a core
responsibility. This scaffold ships the mixin (the load-bearing piece) and
**deliberately defers the** ``res.config.settings`` **surface** to the first
downstream module that needs a real setting — building the settings group now,
with no consumer, would be speculative surface. When that consumer arrives it
drops a block into ``res.config.settings``; nothing here has to change.

Accounting engine baseline + boundary (F1-T02)
==============================================

"**Odoo owns accounting. NCollection owns the business experience.**"
(FPA §4/§6). This module codifies that boundary two ways.

Baseline config (applied at provisioning)
-----------------------------------------

On install, ``post_init_hook`` (``hooks.py``) calls
``res.company._nc_apply_accounting_baseline()``, which sets the company's
fiscal-year end to the NCollection baseline — **31 December** (the calendar
year, the UAE standard) — on Odoo's own native ``fiscalyear_last_day`` /
``fiscalyear_last_month`` fields. Idempotent and fail-soft (its own savepoint),
so it can never break tenant provisioning.

What it does **not** touch, on purpose:

- **Journals** — created by the chart of accounts (Odoo / ``l10n_ae``, #45). We
  never create or redefine journals.
- **Lock dates** (``fiscalyear_lock_date``, ``tax_lock_date``,
  ``sale_lock_date``, ``purchase_lock_date``) — left **operator-controlled**.
  Auto-locking a fresh tenant's open periods would be wrong; closing periods is
  an accountant's decision, exposed through Odoo's own Settings.

Engine-boundary guard (a test that bites)
-----------------------------------------

``tests/test_engine_boundary.py`` fails CI if **any** ``ncollection_*`` module
overrides a core posting / tax / reconcile **computation** method on the engine:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Model
     - Guarded methods (must not be overridden)
   * - ``account.move``
     - ``_post`` · ``_compute_tax_totals`` · ``_reverse_moves``
   * - ``account.move.line``
     - ``_compute_balance`` · ``_compute_amount_currency`` · ``reconcile``
   * - ``account.tax``
     - ``compute_all``
   * - ``account.payment``
     - ``_synchronize_to_moves``

Allowed, and encouraged, is the opposite: **extend** the engine with new fields
or **new** methods, and wrap workflow entry points (``action_post``,
``button_draft`` …) via ``super()`` — NCollection legitimately owns approval
chains, notifications and closing workflows (FPA §6). Only reimplementing the
computation is forbidden; crossing that line needs explicit architectural
approval (FPA §6).

Dependencies
============

``account`` (the Odoo engine it extends) · ``ncollection_core`` (home of
``ncollection.workspace.config`` + the Ring-2 enforcer it reads).
``ncollection_core`` ships in every tenant, so this adds no new footprint.
