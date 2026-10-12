# -*- coding: utf-8 -*-
"""Applications: what the plan includes, what is installed, and what shows on Home.

THREE SEPARATE FACTS, never one flag:

* ENTITLED   - the platform's decision (``ncollection.workspace.config.allowed_module_names``, synced from the
               tenant's plan, plus its dependency closure). Read-only here. A tenant can never entitle itself.
* INSTALLED  - the module's state in THIS database. Installation is the platform's job too (an isolated
               ``odoo -i`` run when the plan changes); the lockout in ``ir_module.py`` stays exactly as it was.
* VISIBLE    - the one thing the workspace Owner controls: whether an installed, entitled application is shown on
               the Home launcher, and in what order. Hiding changes NOTHING else: menus, access rights, licensing
               and data are untouched (the sidebar and every direct link still work for those who hold the rights);
               showing grants nothing.

Combinations a tenant can be in: installed+entitled+visible, installed+entitled+hidden, entitled but not yet
installed (the platform installs it), and not entitled at all.

This model is only a per-application preference + a status board. It is filled by :meth:`_nc_sync` (called when the
Owner opens the screen) from ``ir.module.module`` and the workspace config, so there is no second module list to
keep in step. ``create`` / ``unlink`` are superuser-only and ``write`` accepts exactly the two preference fields,
all enforced here at the ORM (Standing Rule 4), not only by hiding buttons.
"""
from odoo import api, fields, models
from odoo.exceptions import AccessError

# Modules that are never a customer application on a tenant's launcher / status board.
_NEVER_LISTED = frozenset({'base', 'ncollection_core', 'ncollection_branding', 'ncollection_auth',
                           'ncollection_saas', 'ncollection_subscription', 'ncollection_billing',
                           'ncollection_reseller'})
_OWNER_FIELDS = frozenset({'visible', 'sequence'})


class NcLauncherApp(models.Model):
    _name = 'ncollection.launcher.app'
    _description = 'Application on the Home launcher'
    _order = 'sequence_sort, module_name, id'

    module_name = fields.Char(required=True, readonly=True, index=True)
    name = fields.Char(compute='_compute_name', string='Application', search='_search_name')
    icon = fields.Binary(compute='_compute_name', string='Icon')
    state = fields.Selection(
        [('active', 'Active'),
         ('pending', 'In your plan, being installed'),
         ('locked', 'Not in your plan')],
        string='Status', readonly=True, default='locked',
        help="Active: in your plan and installed. In your plan, being installed: the platform installs it. "
             "Not in your plan: change your subscription to add it.")
    visible = fields.Boolean(
        string='Show on Home', default=True,
        help="Show this application on the Home screen. Hiding it only removes the tile: access, menus and data "
             "are unchanged.")
    sequence = fields.Integer(
        string='Home order', default=0,
        help="0 keeps the natural order. Give applications 1, 2, 3 ... to put them first, in that order.")
    sequence_sort = fields.Integer(compute='_compute_sequence_sort', store=True)

    _module_uniq = models.Constraint('unique(module_name)', 'An application is listed once.')

    @api.depends('sequence')
    def _compute_sequence_sort(self):
        for app in self:
            app.sequence_sort = app.sequence if app.sequence > 0 else 1_000_000

    def _search_name(self, operator, value):
        modules = self.env['ir.module.module'].sudo().search(
            ['|', ('shortdesc', operator, value), ('name', operator, value)])
        return [('module_name', 'in', modules.mapped('name'))]

    def _compute_name(self):
        modules = self.env['ir.module.module'].sudo().search([('name', 'in', self.mapped('module_name'))])
        by_name = {m.name: m for m in modules}
        for app in self:
            module = by_name.get(app.module_name)
            app.name = (module.shortdesc if module else '') or app.module_name
            app.icon = module.icon_image if module else False

    # ------------------------------------------------------------------ the ORM boundary
    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(self.env._("The application list is maintained by the workspace itself."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and set(vals) - _OWNER_FIELDS:
            raise AccessError(self.env._("Only whether an application shows on Home, and its order, can be changed."))
        return super().write(vals)

    def unlink(self):
        if not self.env.su:
            raise AccessError(self.env._("The application list is maintained by the workspace itself."))
        return super().unlink()

    # ------------------------------------------------------------------ the status board
    @api.model
    def _nc_candidate_modules(self):
        """The customer-facing applications this database knows: ``application=True``, not an NCollection platform /
        core module, installable (no missing dependency)."""
        Module = self.env['ir.module.module'].sudo()
        modules = Module.search([('application', '=', True), ('state', 'in', ('installed', 'uninstalled'))])
        return modules.filtered(
            lambda m: m.name not in _NEVER_LISTED
            and not m.name.startswith('ncollection_')
            and not any(dep.state == 'unknown' for dep in m.dependencies_id))

    @api.model
    def _nc_entitled_modules(self):
        """Names the plan entitles, with the dependency closure, or ``None`` meaning "no restriction" (an empty list
        is fail-open everywhere else in the workspace, so it is here too)."""
        config = self.env['ncollection.workspace.config'].sudo().get_config()
        allowed = config.get_allowed_module_list() if config else []
        if not allowed:
            return None
        return self.env['ir.ui.menu']._ncollection_expand_dependencies(allowed)

    @api.model
    def _nc_sync(self):
        """Bring the board in line with reality: one row per candidate application, status recomputed. Preferences
        (visible / order) of an existing row are never touched. Runs as the system on purpose: the Owner is not
        allowed to create or delete rows."""
        self = self.sudo()
        # two Owners opening the screen at once must not both create the same row (unique(module_name))
        self.env.cr.execute("SELECT pg_advisory_xact_lock(hashtext('ncollection.launcher.app.sync'))")
        candidates = self._nc_candidate_modules()
        entitled = self._nc_entitled_modules()
        rows = {row.module_name: row for row in self.search([])}
        for module in candidates:
            installed = module.state == 'installed'
            is_entitled = entitled is None or module.name in entitled
            state = 'active' if (installed and is_entitled) else ('pending' if is_entitled else 'locked')
            row = rows.pop(module.name, None)
            if row is None:
                self.create({'module_name': module.name, 'state': state})
            elif row.state != state:
                row.write({'state': state})
        for stale in rows.values():           # a module that is no longer an application of this database
            stale.unlink()

    @api.model
    def action_open_manager(self):
        """Settings > Applications. Refreshes the board, then opens it. A public RPC entry point, so it is refused to
        everyone who could not open the screen: the workspace Owner and system administrators only."""
        user = self.env.user
        if not (self.env.su or user.has_group('ncollection_core.group_role_owner')
                or user.has_group('base.group_system')):
            raise AccessError(self.env._("Only the workspace Owner can manage applications."))
        self._nc_sync()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Applications"),
            'res_model': self._name,
            'view_mode': 'list',
            'views': [(False, 'list')],
            'context': {'search_default_in_plan': 1},
            'target': 'current',
        }

    # ------------------------------------------------------------------ the launcher
    @api.model
    def _nc_hidden_modules(self):
        """Modules the Owner chose to hide from the Home screen (read as the system: this is a preference, not data
        any user may be refused)."""
        return set(self.sudo().search([('visible', '=', False)]).mapped('module_name'))

    @api.model
    def _nc_home_order(self):
        """``{module_name: sequence}`` for the applications the Owner ordered explicitly (sequence > 0)."""
        return {row.module_name: row.sequence for row in self.sudo().search([('sequence', '>', 0)])}
