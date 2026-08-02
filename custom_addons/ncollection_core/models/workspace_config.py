# -*- coding: utf-8 -*-
"""Per-tenant workspace configuration (P1-T09).

One record per tenant database, written by the provisioning pipeline
(P2-T01) and kept in sync on plan changes (P2-T03). The tenant itself
never edits it (ACL: write restricted to base.group_system).

This model feeds Ring 1 of license defense-in-depth (menu visibility,
see models/ir_ui_menu.py). Ring 2 (ORM enforcement, P1-T10) will consume
the same record.
"""

import logging

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

# The only fields the platform config-sync channel (P2-T03) may push. Any other
# key in an inbound sync payload is rejected — the RPC service account can write
# nothing else on this model, and this method is its single entry point.
# Tenant-side crons the PLATFORM depends on staying alive (#262).
#
# Odoo deactivates a cron by itself after MIN_FAILURE_COUNT_BEFORE_DEACTIVATION
# (5) failures spanning MIN_DELTA_BEFORE_DEACTIVATION (7 days) — verified in
# odoo/addons/base/models/ir_cron.py. So a misconfigured compliance job does not
# just fail loudly, it eventually switches ITSELF OFF and goes quiet. A tenant
# module cannot see the fleet, so the tenant reports and the platform watches.
#
# Referenced by xml-id and resolved softly: ncollection_core must not depend on
# ncollection_auth (a tenant may legitimately not have it yet — see #218), so an
# unresolvable id means "not installed", which is NOT a fault.
REQUIRED_CRON_XMLIDS = (
    'ncollection_auth.cron_gc_auth_log',
)

_SYNCABLE_FIELDS = frozenset({
    'allowed_module_names', 'plan_code', 'subscription_status', 'max_users',
})

# White-label reseller branding (P3/P10-T09): the platform may cascade a
# reseller's brand onto the tenant's company alongside the licensing sync. These
# keys are NOT stored on workspace.config — they are applied to res.company's
# nc_* branding fields (owned by ncollection_branding, on which this module
# already depends), then dropped before the licensing whitelist runs. Applied
# override-if-default so a tenant that later customises its own theme is never
# clobbered by a reconcile push. Same authenticated per-tenant channel + the same
# res.company ORM write (so the hex ValidationError still guards CSS injection).
_BRAND_APPLY_FIELDS = {
    'brand_primary_color': 'nc_primary_color',
    'brand_secondary_color': 'nc_secondary_color',
    'brand_sidebar_color': 'nc_sidebar_color',
    'brand_login_background': 'nc_login_background',
}


class WorkspaceConfig(models.Model):
    _name = 'ncollection.workspace.config'
    _description = 'NCollection Workspace Configuration'

    allowed_module_names = fields.Text(
        string='Allowed Modules',
        help='Comma-separated technical names of the menu-owning modules '
             'licensed by the plan, e.g. "crm,sale,account". Use the module '
             'that owns the app\'s ROOT MENU xml-id (sale, not '
             'sale_management). Empty = no filtering (fail-open).',
    )
    plan_code = fields.Char(
        help='Plan code as defined on the platform (e.g. STARTER).',
    )
    subscription_status = fields.Char(
        help='Subscription status pushed by the platform (e.g. trial, '
             'active, suspended, expired). Deliberately a free Char: the '
             'tenant addon must not couple to platform-layer enums.',
    )
    max_users = fields.Integer(
        help='Maximum number of ACTIVE internal users allowed by the plan, '
             'pushed by the platform (P2-T01/P2-T03 contract, like the other '
             'fields here). 0 or unset = unlimited (fail-open).',
    )

    @api.model_create_multi
    def create(self, vals_list):
        # Access first, invariants second: an unauthorized caller must get
        # AccessError, never a ValidationError that leaks record state.
        self.browse().check_access('create')
        if self.search_count([]) + len(vals_list) > 1:
            raise ValidationError(self.env._(
                'Only one workspace configuration record may exist per '
                'tenant database.'
            ))
        records = super().create(vals_list)
        self.env.registry.clear_cache()
        return records

    def write(self, vals):
        result = super().write(vals)
        # Menu visibility (and its ormcached layers, including
        # ir.ui.menu.load_menus) must recompute after any config change.
        self.env.registry.clear_cache()
        return result

    def unlink(self):
        result = super().unlink()
        self.env.registry.clear_cache()
        return result

    @api.model
    def get_max_users(self):
        """Plan's max active internal users; 0 = unlimited."""
        config = self.sudo().get_config()
        return config.max_users if config else 0

    @api.model
    def get_config(self):
        """Return the singleton config record (empty recordset if absent)."""
        return self.search([], limit=1)

    @api.model
    def sync_from_platform(self, vals):
        """Single entry point for the platform config-sync channel (P2-T03).

        Called over json2/bearer by the dedicated, workspace.config-scoped
        service account. Whitelists the pushable fields (so even a compromised
        service account cannot write anything unexpected on this model), then
        updates the singleton — whose write() clears the tenant registry cache,
        so menu visibility + license enforcement recompute within the SLA.
        Returns a small status dict (json2-serialisable) for the caller to log.
        """
        if not isinstance(vals, dict):
            raise UserError(self.env._("sync_from_platform expects a dict of values."))
        # Separate the optional reseller brand keys from the licensing payload
        # up front so they don't trip the "non-whitelisted field" guard — but
        # only APPLY them at the end, after every validation has passed, so no
        # company row is written for a payload that then raises.
        vals = dict(vals)
        brand = {k: vals.pop(k) for k in list(vals) if k in _BRAND_APPLY_FIELDS}
        rejected = set(vals) - _SYNCABLE_FIELDS
        if rejected:
            raise UserError(self.env._(
                "Refusing to sync non-whitelisted field(s): %s", ', '.join(sorted(rejected))))
        config = self.get_config()
        if not config:
            # The singleton is created once by provisioning; the write-scoped
            # sync account cannot create it. Surface loudly so the platform logs
            # it and the reconcile/seed path repairs it.
            raise UserError(self.env._(
                "No workspace configuration exists to sync into (not provisioned?)."))
        config.write({k: vals[k] for k in vals})
        # Validation passed — now cascade any reseller branding (last, so a
        # rejected payload never leaves a written company row behind).
        if brand:
            self._nc_apply_pushed_branding(brand)
        return {'ok': True, 'plan_code': config.plan_code,
                'subscription_status': config.subscription_status,
                'required_crons': self._required_cron_health()}

    @api.model
    def _required_cron_health(self):
        """Self-report on the crons the platform requires (#262).

        Rides the config-sync RESPONSE rather than adding an endpoint or a
        second nightly fleet pass: the reconcile already visits every ready
        tenant, and a tenant reporting on ITSELF keeps Rule 3 intact — the
        platform never queries a tenant model.

        Shape: ``{xml_id: {'installed': bool, 'active': bool}}``.
        ``installed=False`` means the owning module is absent, which is a
        legitimate state for a tenant provisioned before the module existed
        (#218) — the platform must not treat it as a fault. Never raises: a
        health probe that can break a config push would be worse than the gap
        it reports.
        """
        # Overridable from context so a test can force the unresolvable case
        # without depending on which modules happen to be installed on the
        # database it runs against.
        xml_ids = self.env.context.get('_nc_required_crons') or REQUIRED_CRON_XMLIDS
        report = {}
        for xml_id in xml_ids:
            try:
                cron = self.env.ref(xml_id, raise_if_not_found=False)
                if not cron:
                    report[xml_id] = {'installed': False, 'active': False}
                else:
                    report[xml_id] = {'installed': True,
                                      'active': bool(cron.sudo().active)}
            except Exception:  # pragma: no cover - defensive
                _logger.exception(
                    "Could not read required cron %s for the health report.",
                    xml_id)
                report[xml_id] = {'installed': False, 'active': False,
                                  'error': True}
        return report

    def _nc_apply_pushed_branding(self, brand):
        """Apply a platform-pushed reseller brand onto the tenant company (P10-T09).

        Override-if-default only: a brand value overwrites a company field solely
        when that field still holds its NCollection default (or is empty), so a
        tenant that has set its own colours keeps them across reconcile pushes.
        The write goes through res.company's ORM, so nc_* hex validation still
        rejects any malformed pushed colour (CSS-injection guard, Rule 4/7).
        sudo() is deliberate: the config-sync service account is workspace.config-
        scoped, but this cascade is platform-initiated and already trusted, same
        as the licensing write above.
        """
        company = self.env.company
        if not company:
            return
        Company = self.env['res.company']
        targets = list(_BRAND_APPLY_FIELDS.values())
        # Only fields branding actually declares (guards against a partial
        # install where ncollection_branding's fields are absent).
        targets = [t for t in targets if t in Company._fields]
        if not targets:
            return
        defaults = Company.default_get(targets)
        to_write = {}
        for key, target in _BRAND_APPLY_FIELDS.items():
            value = brand.get(key)
            if not value or target not in targets:
                continue
            current = company[target]
            if not current or current == defaults.get(target):
                to_write[target] = value
        if to_write:
            company.sudo().write(to_write)

    def get_allowed_module_list(self):
        """Parse allowed_module_names into a clean, de-duplicated list.

        Same semantics as ncollection.subscription.plan (P1-T07):
        whitespace stripped, empties dropped, order preserved, dupes removed.
        """
        self.ensure_one()
        if not self.allowed_module_names:
            return []
        seen = set()
        result = []
        for raw in self.allowed_module_names.split(','):
            name = raw.strip()
            if name and name not in seen:
                seen.add(name)
                result.append(name)
        return result
