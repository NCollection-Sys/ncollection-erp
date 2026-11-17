# -*- coding: utf-8 -*-
"""Platform control of the tenant Settings app (platform side of ncollection_core/settings_policy.py).

  plan line     the default level of a section for every tenant on the plan
  tenant line   a per-tenant override that can only LOWER the plan's level (a tenant can never be given more than its
                plan; to give more, change the plan)
  entitlement   a section whose application is not in the tenant's effective module list is hidden
Effective level = min(plan level or 'administer', tenant level if any), then entitlement. Pushed by config-sync as
JSON (`settings_policy`) into the tenant's ncollection.workspace.config. Every change is noted on the tenant's / the
plan's chatter (who, which section, old -> new) and triggers a sync. No tenant database is ever read from here.
"""
import json

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.ncollection_core.models.settings_policy import CAPABILITIES, LEVELS, RANK, SECTIONS

# Section -> the module that must be entitled for it to exist at all.
# Matched against the DEPENDENCY CLOSURE of the tenant's licensed modules, exactly as Ring 1 licenses menus
# (ir.ui.menu._ncollection_expand_dependencies): a plan licenses apps such as `sale_management` / `account`, and
# everything they depend on comes with them. Matching the bare plan list hid Sales Settings on a plan that lists
# `sale_management`, and made the receipts grant impossible (found by the platform-to-tenant integration check).
SECTION_MODULE = {
    'accounting_defaults': 'account',
    'audit_log': 'ncollection_audit',
    'ai': 'ncollection_ai',
    'sales': 'sale',
    'purchase': 'purchase',
    'inventory': 'stock',
    'receipts': 'account',
}
# A capability (core CAPABILITIES) WIDENS a Workspace Administrator's rights, so it is OFF unless a line grants it:
# any level from "view" up grants it, "hidden" (or no line) does not. Pushed as 'granted' / 'denied'.
LEVEL_SELECTION = [('hidden', 'Hidden'), ('view', 'View only'), ('edit', 'Edit'),
                   ('administer', 'Administer (and delegate)')]


class SettingsPolicyLine(models.Model):
    _name = 'ncollection.settings.policy.line'
    _description = 'Tenant Settings policy line'
    _order = 'section'

    plan_id = fields.Many2one('ncollection.subscription.plan', ondelete='cascade', index=True)
    tenant_id = fields.Many2one('ncollection.tenant', ondelete='cascade', index=True)
    section = fields.Selection([(key, label) for key, label, _m, _d in SECTIONS]
                               + [(key, label) for key, (label, _g) in CAPABILITIES.items()], required=True)
    level = fields.Selection(LEVEL_SELECTION, required=True, default='administer')

    _one_per_owner = models.Constraint(
        'UNIQUE NULLS NOT DISTINCT (plan_id, tenant_id, section)', 'One line per section.')

    @api.constrains('plan_id', 'tenant_id', 'section', 'level')
    def _check_owner_and_ceiling(self):
        for line in self:
            if bool(line.plan_id) == bool(line.tenant_id):
                raise ValidationError(self.env._("A policy line belongs to a plan OR to a tenant."))
            if line.tenant_id:
                plan = {p.section: p.level for p in line.tenant_id.plan_id.settings_policy_line_ids}
                ceiling = plan.get(line.section, 'hidden' if line.section in CAPABILITIES else 'administer')
                if RANK[line.level] > RANK[ceiling]:
                    raise ValidationError(self.env._(
                        "%(section)s: a tenant override cannot give more than its plan (%(plan)s).",
                        section=line._nc_label(), plan=ceiling))

    def _nc_tenants(self):
        """The tenants these lines govern (a plan line governs every tenant on the plan)."""
        return self.tenant_id | self.env['ncollection.tenant'].search([('plan_id', 'in', self.plan_id.ids)])

    def _nc_note(self, text):
        """Audited on every affected tenant's chatter (plans have none): who, which section, old -> new."""
        for line in self:
            scope = self.env._('plan %s', line.plan_id.name) if line.plan_id else self.env._('this tenant')
            for tenant in line._nc_tenants():
                tenant.message_post(body=self.env._("%(text)s (%(scope)s)", text=text(line), scope=scope),
                                    author_id=self.env.user.partner_id.id, subtype_xmlid='mail.mt_note')

    def _nc_resync(self, tenants=None):
        for tenant in (tenants if tenants is not None else self._nc_tenants()).filtered(
                lambda t: t.database_status == 'ready'):
            tenant._config_sync_enqueue()

    def _nc_label(self):
        return dict(self._fields['section'].selection)[self.section]

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._nc_note(lambda ln: self.env._("Settings policy set: %(s)s = %(v)s", s=ln._nc_label(), v=ln.level))
        lines._nc_resync()
        return lines

    def write(self, vals):
        old = {line.id: line.level for line in self}
        res = super().write(vals)
        for line in self:
            line._nc_note(lambda ln: self.env._("Settings policy changed: %(s)s %(o)s -> %(v)s",
                                                s=ln._nc_label(), o=old.get(ln.id), v=ln.level))
        self._nc_resync()
        return res

    def unlink(self):
        tenants = self._nc_tenants()
        self._nc_note(lambda ln: self.env._("Settings policy removed: %(s)s (back to the default)", s=ln._nc_label()))
        res = super().unlink()
        self._nc_resync(tenants)
        return res


class SubscriptionPlan(models.Model):
    _inherit = 'ncollection.subscription.plan'

    settings_policy_line_ids = fields.One2many('ncollection.settings.policy.line', 'plan_id',
                                               string='Settings policy')


class Tenant(models.Model):
    _inherit = 'ncollection.tenant'

    settings_policy_line_ids = fields.One2many('ncollection.settings.policy.line', 'tenant_id',
                                               string='Settings overrides')
    settings_policy_effective = fields.Text(compute='_compute_settings_policy_effective',
                                            string='Effective Settings policy')

    def _nc_settings_policy(self):
        """{section: level}, or {} when neither the plan nor the tenant says anything (legacy: everything
        administer, and the key is not even sent, so a tenant not yet upgraded keeps syncing)."""
        self.ensure_one()
        plan_lines = {line.section: line.level for line in self.plan_id.settings_policy_line_ids}
        tenant_lines = {line.section: line.level for line in self.settings_policy_line_ids}
        if not plan_lines and not tenant_lines:
            return {}
        modules = self.env['ir.ui.menu']._ncollection_expand_dependencies(set(self._nc_effective_module_list()))
        out = {}
        for key, _label, _m, _d in SECTIONS:
            level = plan_lines.get(key, 'administer')
            if key in tenant_lines and RANK[tenant_lines[key]] < RANK[level]:
                level = tenant_lines[key]
            needed = SECTION_MODULE.get(key)
            if needed and needed not in modules:
                level = 'hidden'
            out[key] = level
        for key in CAPABILITIES:
            level = plan_lines.get(key, 'hidden')
            if key in tenant_lines and RANK[tenant_lines[key]] < RANK[level]:
                level = tenant_lines[key]
            needed = SECTION_MODULE.get(key)
            granted = level != 'hidden' and not (needed and needed not in modules)
            out[key] = 'granted' if granted else 'denied'
        return out

    @api.depends('plan_id.settings_policy_line_ids.level', 'settings_policy_line_ids.level')
    def _compute_settings_policy_effective(self):
        for tenant in self:
            policy = tenant._nc_settings_policy()
            tenant.settings_policy_effective = '\n'.join(
                '%s: %s' % (label, policy.get(key, 'administer')) for key, label, _m, _d in SECTIONS)

    def _config_sync_vals(self):
        vals = super()._config_sync_vals()
        policy = self._nc_settings_policy()
        if policy:
            vals['settings_policy'] = json.dumps(policy, sort_keys=True)
        return vals


assert set(LEVELS) == {key for key, _l in LEVEL_SELECTION}
