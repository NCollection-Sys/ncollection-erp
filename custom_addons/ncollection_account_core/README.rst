=======================
NCollection Account Core
=======================

**Task:** F1-T01 · **Architecture:** ``FINANCIAL_PLATFORM_ARCHITECTURE.md`` §7

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

Dependencies
============

``account`` (the Odoo engine it extends) · ``ncollection_core`` (home of
``ncollection.workspace.config`` + the Ring-2 enforcer it reads).
``ncollection_core`` ships in every tenant, so this adds no new footprint.
