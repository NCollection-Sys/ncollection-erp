# -*- coding: utf-8 -*-
"""The tenant's Modules tab: a structured entitlement table and a toggle wizard.

NOT A SECOND ENTITLEMENT SYSTEM. Everything here is a view over, or an editor
for, the two things that already exist:

  * the plan's module list (``ncollection.subscription.plan``), the shared
    baseline every tenant on that plan inherits, and
  * ``ncollection.tenant.module.override``, the per-tenant delta.

``_nc_effective_module_list`` stays the single authority for what a tenant may
use. The matrix READS it; the wizard WRITES overrides (create / update / delete
of the same override rows an administrator could have typed by hand), so a
change reaches the tenant by exactly the route an override always has: queued,
deduplicated and retryable. Nothing here touches a plan, so customising tenant A
cannot change tenant B — the plan is never written.
"""
from markupsafe import Markup, escape

from odoo import Command, api, fields, models
from odoo.exceptions import UserError

from .companions import COMPANION_MODULES


class TenantModuleMatrix(models.Model):
    _inherit = 'ncollection.tenant'

    module_matrix_html = fields.Html(
        string='Module Entitlement', compute='_compute_module_matrix_html',
        sanitize=False,
        help="What the plan grants, what this tenant has changed, and what the "
             "tenant ends up with. Read-only: change it with 'Customize modules'.")

    def _nc_module_rows(self):
        """One dict per module that matters for this tenant, sorted by name.

        ``in_plan``   - the baseline grants it (plan, or the country package)
        ``locked``    - a localization module or a companion: cannot be switched off
        ``follows``   - for a companion, the module whose entitlement it follows
        ``override``  - '' / 'enable' / 'disable'
        ``effective`` - what the tenant actually gets (from the one authority)
        """
        self.ensure_one()
        Module = self.env['ir.module.module'].sudo()
        plan_modules = self.plan_id.get_allowed_module_list() if self.plan_id else []
        localization = set(self._nc_localization_modules())
        overrides = {o.module_name: o for o in self.module_override_ids}
        effective = set(self._nc_effective_module_list())
        # companions that apply (cheque printing follows Accounting): shown, but with no switch of their own
        companions = {name: host for name, host in COMPANION_MODULES.items() if name in effective}
        names = list(dict.fromkeys(
            plan_modules + sorted(localization) + sorted(companions) + list(overrides)))
        labels = {m.name: (m.shortdesc or m.name)
                  for m in Module.search([('name', 'in', names + list(companions.values()))])}
        rows = []
        for name in names:
            override = overrides.get(name)
            rows.append({
                'name': name,
                'label': labels.get(name, name),
                'in_plan': name in plan_modules or name in localization or name in companions,
                'locked': name in localization or name in companions,
                'follows': labels.get(companions[name], companions[name]) if name in companions else '',
                'override': override.mode if override else '',
                'reason': override.reason if override else '',
                'effective': name in effective,
            })
        rows.sort(key=lambda r: r['label'].lower())
        return rows

    @api.depends('plan_id', 'plan_id.allowed_module_names', 'country_id',
                 'module_override_ids.module_name', 'module_override_ids.mode',
                 'module_override_ids.active', 'module_override_ids.reason')
    def _compute_module_matrix_html(self):
        for tenant in self:
            # A not-yet-saved record has nothing to show; the wizard needs an id.
            if not tenant.id:
                tenant.module_matrix_html = False
                continue
            tenant.module_matrix_html = tenant._nc_render_module_matrix(
                tenant._nc_module_rows())

    def _nc_render_module_matrix(self, rows):
        _ = self.env._
        if not rows:
            return Markup(
                '<div class="o_nc_modmatrix_empty text-center text-muted py-5">'
                '<div class="fs-4 mb-2">%s</div><div>%s</div></div>') % (
                    escape(_("No optional modules yet")),
                    escape(_("This tenant's plan grants only the core modules "
                             "every workspace receives. Use “Customize modules” "
                             "to add modules for this tenant alone.")))
        badge = Markup('<span class="badge rounded-pill text-bg-%s">%s</span>')
        out = [Markup(
            '<table class="o_nc_modmatrix table table-sm align-middle mb-0">'
            '<thead><tr class="text-muted small">'
            '<th>%s</th><th>%s</th><th>%s</th><th>%s</th></tr></thead><tbody>') % (
                escape(_("Module")), escape(_("From the plan")),
                escape(_("This tenant")), escape(_("Result")))]
        for row in rows:
            if row['follows']:
                plan_cell = badge % ('secondary', escape(self.env._("Follows %s", row["follows"])))
            elif row['locked']:
                plan_cell = badge % ('secondary', escape(_("Country package")))
            elif row['in_plan']:
                plan_cell = badge % ('success', escape(_("Included")))
            else:
                plan_cell = Markup('<span class="text-muted">—</span>')
            if row['override'] == 'enable':
                tenant_cell = badge % ('info', escape(_("Added")))
            elif row['override'] == 'disable' and not row['locked']:
                tenant_cell = badge % ('warning', escape(_("Removed")))
            else:
                tenant_cell = Markup('<span class="text-muted">—</span>')
            result = (badge % ('success', escape(_("Enabled"))) if row['effective']
                      else badge % ('danger', escape(_("Disabled"))))
            reason = (Markup('<div class="small text-muted">%s</div>')
                      % escape(row['reason'])) if row['reason'] else Markup('')
            out.append(Markup(
                '<tr><td><div class="fw-semibold">%s</div>'
                '<div class="small text-muted font-monospace">%s</div>%s</td>'
                '<td>%s</td><td>%s</td><td>%s</td></tr>') % (
                    escape(row['label']), escape(row['name']), reason,
                    plan_cell, tenant_cell, result))
        out.append(Markup('</tbody></table>'))
        return Markup('').join(out)

    def action_customize_modules(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Customize modules — %s", self.company_name),
            'res_model': 'ncollection.tenant.module.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_tenant_id': self.id},
        }


class TenantModuleWizard(models.TransientModel):
    _name = 'ncollection.tenant.module.wizard'
    _description = 'Customize Modules For One Tenant'

    tenant_id = fields.Many2one('ncollection.tenant', required=True, readonly=True)
    reason = fields.Char(
        help="Recorded on every override this change creates, so 'why is this "
             "customer different from their plan?' always has an answer.")
    line_ids = fields.One2many(
        'ncollection.tenant.module.wizard.line', 'wizard_id', string='Modules')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        tenant = self.env['ncollection.tenant'].browse(
            res.get('tenant_id') or self.env.context.get('default_tenant_id'))
        if not tenant.exists():
            return res
        rows = {r['name']: r for r in tenant._nc_module_rows()}
        catalog = self.env['ncollection.subscription.plan'].get_selectable_modules()
        lines = []
        # Everything an operator could switch on: the optional catalog (real
        # apps only), plus anything the plan or an override already names.
        seen = set()
        for entry in catalog['optional']:
            seen.add(entry['name'])
            row = rows.get(entry['name'])
            lines.append(Command.create({
                'module_name': entry['name'],
                'label': entry['label'],
                'in_plan': bool(row and row['in_plan']),
                'locked': bool(row and row['locked']),
                'enabled': bool(row and row['effective']),
            }))
        for name, row in rows.items():
            if name in seen:
                continue
            lines.append(Command.create({
                'module_name': name, 'label': row['label'],
                'in_plan': row['in_plan'], 'locked': row['locked'],
                'enabled': row['effective'],
            }))
        res.update({'tenant_id': tenant.id, 'line_ids': lines})
        return res

    def action_apply(self):
        """Turn the toggles into override rows — and nothing else.

        A line whose toggle matches the plan needs NO override (and any old one
        is removed, which is what restores the plan's answer exactly). A line
        that differs becomes 'added' (switched on, not in the plan) or 'removed'
        (switched off, in the plan). The plan itself is never written.
        """
        self.ensure_one()
        if not (self.env.su or self.env.user.has_group(
                'ncollection_subscription.group_platform_admin')):
            raise UserError(self.env._(
                "Only a platform administrator can change a tenant's modules."))
        Override = self.env['ncollection.tenant.module.override'].sudo().with_context(
            active_test=False)
        existing = {o.module_name: o
                    for o in Override.search([('tenant_id', '=', self.tenant_id.id)])}
        for line in self.line_ids:
            if line.locked:
                continue
            name = line.module_name
            if line.enabled == line.in_plan:
                wanted = None
            else:
                wanted = 'enable' if line.enabled else 'disable'
            current = existing.get(name)
            if wanted is None:
                if current:
                    current.unlink()
            elif current:
                if current.mode != wanted or not current.active:
                    current.write({
                        'mode': wanted, 'active': True,
                        'reason': self.reason or current.reason})
            else:
                Override.create({
                    'tenant_id': self.tenant_id.id, 'module_name': name,
                    'mode': wanted, 'reason': self.reason})
        return {'type': 'ir.actions.act_window_close'}


class TenantModuleWizardLine(models.TransientModel):
    _name = 'ncollection.tenant.module.wizard.line'
    _description = 'Customize Modules — One Module'
    _order = 'label, id'

    wizard_id = fields.Many2one(
        'ncollection.tenant.module.wizard', required=True, ondelete='cascade')
    module_name = fields.Char(string='Technical Name', readonly=True)
    label = fields.Char(string='Module', readonly=True)
    in_plan = fields.Boolean(string='In the plan', readonly=True)
    locked = fields.Boolean(readonly=True)
    enabled = fields.Boolean(string='Enabled for this tenant')
    change = fields.Selection(
        [('added', 'Added'), ('removed', 'Removed')],
        string='Change', compute='_compute_change')

    @api.depends('enabled', 'in_plan', 'locked')
    def _compute_change(self):
        for line in self:
            if line.locked or line.enabled == line.in_plan:
                line.change = False
            else:
                line.change = 'added' if line.enabled else 'removed'
