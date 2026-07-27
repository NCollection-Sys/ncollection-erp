# -*- coding: utf-8 -*-
"""Shared financial mixin for the ncollection_account_* family (F1-T01).

`ncollection_account_core` is the thin base layer between Odoo's ``account``
engine and the rest of the financial platform (reports, dashboard, analytics,
approval, budget, assets, documents, audit, localization). Per
FINANCIAL_PLATFORM_ARCHITECTURE.md §7 it owns SHARED glue only — it must never
own reports, dashboards, budget, analytics, audit or assets, and it never
duplicates accounting-engine logic (Odoo owns posting, journals and taxes).

The load-bearing contribution is ``ncollection.account.mixin``: the common
extension point every downstream ``ncollection_account_*`` model mixes in to
gate its features by the tenant's subscription plan.

Crucially, the gate READS the same source of truth as the platform's Ring-2 ORM
license enforcement (#11, in ``ncollection_core``): the per-tenant
``ncollection.workspace.config`` singleton, via its ``get_allowed_module_list``
contract. It does NOT stand up a second enforcement path — Ring-2 already denies
CRUD on unlicensed namespaces at ``_check_access``. These helpers are an opt-in,
branded "upsell early" convenience for imperative code (wizard actions, server
actions, buttons) that wants to fail fast and legibly *before* the ORM would.
"""

from odoo import api, models
from odoo.exceptions import AccessError


class NcollectionAccountMixin(models.AbstractModel):
    _name = 'ncollection.account.mixin'
    _description = 'NCollection Financial Shared Mixin'

    @api.model
    def _nc_feature_enabled(self, module_name):
        """Return True if ``module_name`` is licensed by the tenant's plan.

        ``module_name`` must be a **plan-priced application namespace** — the
        menu-owning module of an app, e.g. ``'account'`` or ``'sale'`` (the same
        value the plan's allowed list holds). Passing an infrastructure
        namespace (``'base'``, ``'ir'``, ``'res'``, ``'mail'``, ``'web'`` or an
        ``'ncollection*'`` module) is a contract violation: Ring-2 never gates
        those, so this helper is not meant to be asked about them and would
        conservatively report them disabled if the plan's list omits them.

        Behaviour mirrors Ring-2 (ncollection_core ``license_enforcement.py``):

        - **System/sudo callers are exempt** (return True) — license enforcement
          applies to non-system users only, so a technical admin is never gated
          here either.
        - Otherwise the answer is ``module_name`` membership in
          ``ncollection.workspace.config.get_allowed_module_list()`` — the exact
          allowed list Ring-2 keys on, so the two never disagree for a priced
          app namespace.
        - **Fail-open** (house style): no workspace config, or an empty allowed
          list (= "no filtering"), reads as enabled. The worst regression is
          "not gated" (today's behaviour on an unconfigured tenant), never a
          bricked workspace.

        :raises ValueError: if ``module_name`` is missing/blank (a programmer
            error — the whole point of the helper is a clear, early failure).
        """
        module_name = (module_name or '').strip()
        if not module_name:
            raise ValueError(
                "module_name is required — pass a plan-priced application "
                "namespace such as 'account' or 'sale'.")
        # Mirror Ring-2: system/sudo users are never license-gated.
        if self.env.su or self.env.user._is_system():
            return True
        config = self.env['ncollection.workspace.config'].sudo().get_config()
        if not config:
            return True
        allowed = config.get_allowed_module_list()
        if not allowed:
            return True
        return module_name in allowed

    @api.model
    def _nc_require_feature(self, module_name):
        """Raise a branded upsell ``AccessError`` when ``module_name`` is not
        licensed by the tenant's plan; return silently when it is.

        For imperative call sites (wizard/server actions, button handlers) that
        want to stop early with a clear message. It consults
        :meth:`_nc_feature_enabled` and so inherits its system-user exemption
        and fail-open behaviour — it MIRRORS Ring-2 and never widens access, it
        only fails faster and more legibly than the raw ORM denial would.
        """
        if not self._nc_feature_enabled(module_name):
            raise AccessError(self.env._(
                "The '%s' feature is not included in your NCollection plan. "
                "Upgrade your subscription to unlock it.", module_name))
