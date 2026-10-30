# -*- coding: utf-8 -*-
"""Eligibility of a tenant for a gated module (see gated_modules.py for the reasoning)."""

from odoo import models
from odoo.exceptions import UserError

from .gated_modules import GATED_MODULES, gated_rule, is_gated


class TenantEligibility(models.Model):
    _inherit = 'ncollection.tenant'

    def _nc_books_verified(self):
        """True only when this tenant's accounting company is VERIFIED to be on its country package's chart/currency.

        Fail-closed default: the platform cannot read a tenant's company (Rule 3), so unless a module that owns the
        verification (ncollection_saas) says so, nothing is verified and no gated module is ever eligible."""
        self.ensure_one()
        return False

    def _nc_module_eligibility(self, module_name):
        """``(eligible, reasons)`` for ``module_name`` on this tenant; ``reasons`` is empty when eligible.

        A module that is not gated is always eligible. Reasons are plain sentences for an operator, one per failed
        condition, so a refusal says exactly what to fix."""
        self.ensure_one()
        rule = gated_rule(module_name)
        if not rule:
            return True, []
        reasons = []
        country = (self.country_id.code or '').upper()
        if country != rule['country']:
            reasons.append(
                self.env._(
                    "%(label)s is for %(country)s tenants only; this tenant's country is %(actual)s.",
                    label=rule['label'],
                    country=rule['country'],
                    actual=country or self.env._("not set"),
                )
            )
        elif not self._nc_books_verified():
            reasons.append(
                self.env._(
                    "This tenant's country is %(country)s but its accounting company has not been verified as a "
                    "%(country)s company (the registry label is not enough). Run 'Apply localization' on the tenant: "
                    "it is a harmless check on a tenant that is already correct, and it refuses one that is not.",
                    country=rule['country'],
                )
            )
        # a requirement that is itself gated (an ASP adapter needs the e-invoicing engine) counts only when that module
        # is entitled AND eligible itself, so the dependency can never be met by a module the tenant may not have
        entitled = set(self._nc_entitlement_before_gate())
        missing = [
            name for name in rule['requires']
            if name not in entitled or (is_gated(name) and not self._nc_module_eligibility(name)[0])
        ]
        if missing:
            reasons.append(
                self.env._(
                    "%(label)s needs %(missing)s in this tenant's entitlement.",
                    label=rule['label'],
                    missing=', '.join(missing),
                )
            )
        return not reasons, reasons

    def _nc_apply_eligibility_gate(self, modules):
        """``modules`` minus every gated module this tenant is not (or no longer) eligible for."""
        self.ensure_one()
        return [name for name in modules if name not in GATED_MODULES or self._nc_module_eligibility(name)[0]]

    # ------------------------------------------------------------------ explicit, per-tenant, idempotent, reversible
    def _nc_enable_gated_module(self, module_name, reason):
        """Activate a gated module for THIS tenant: a get-or-create of the per-tenant ``enable`` override.

        One tenant at a time by construction (``ensure_one``): there is no bulk variant, no cron and no 'all tenants of
        a country'. Running it twice leaves one override row and the same entitlement (the (tenant, module) pair is
        unique and the entitlement push is deduplicated). The override's own constraint refuses an ineligible tenant.
        A reason (ticket / approval reference) is mandatory because 'who enabled this, and why' is the first question
        asked about a regulated feature."""
        self.ensure_one()
        if not is_gated(module_name):
            raise UserError(self.env._("'%s' is not a gated module.", module_name))
        if not (reason or '').strip():
            raise UserError(
                self.env._("A reason (a ticket or approval reference) is required to enable %s.", module_name)
            )
        Override = self.env['ncollection.tenant.module.override'].with_context(active_test=False)
        existing = Override.search([('tenant_id', '=', self.id), ('module_name', '=', module_name)])
        if existing:
            existing.write({'mode': 'enable', 'active': True, 'reason': reason.strip()})
            return existing
        return Override.create(
            {'tenant_id': self.id, 'module_name': module_name, 'mode': 'enable', 'reason': reason.strip()}
        )

    def _nc_withdraw_gated_module(self, module_name):
        """Reverse ``_nc_enable_gated_module``: archive the override. The licence goes through the existing push;
        nothing is uninstalled and no data is touched (revocation is not uninstallation)."""
        self.ensure_one()
        existing = (
            self.env['ncollection.tenant.module.override']
            .with_context(active_test=False)
            .search([('tenant_id', '=', self.id), ('module_name', '=', module_name)])
        )
        existing.write({'active': False})
        return existing
