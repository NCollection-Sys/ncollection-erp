# -*- coding: utf-8 -*-
"""The Workspace Administration overview: a read-only summary computed from the real records when it is opened.

Nothing is stored or maintained here (a TransientModel recomputed on every visit), so it cannot go stale or differ from
the Users, Roles and Security screens it links to.
"""
from odoo import api, fields, models

_OWNER_GROUP = 'ncollection_core.group_role_owner'


class WorkspaceOverview(models.TransientModel):
    _name = 'ncollection.workspace.overview'
    _description = 'Workspace administration overview'

    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, readonly=True)
    plan_code = fields.Char(compute='_compute_figures', string='Plan')
    subscription_status = fields.Char(compute='_compute_figures', string='Subscription')
    max_users = fields.Integer(compute='_compute_figures', string='Users allowed by the plan')
    active_users = fields.Integer(compute='_compute_figures', string='Active users')
    archived_users = fields.Integer(compute='_compute_figures', string='Deactivated users')
    administrators = fields.Integer(compute='_compute_figures')
    pending_password_changes = fields.Integer(compute='_compute_figures', string='Waiting to choose a new password')
    standard_roles = fields.Integer(compute='_compute_figures', string='Standard roles available')
    custom_roles = fields.Integer(compute='_compute_figures', string='Custom roles')
    has_audit_log = fields.Boolean(compute='_compute_figures', string='Audit log available')
    admin_logo = fields.Binary(compute='_compute_admin_logo', string='Administration logo')

    def _compute_admin_logo(self):
        # the SYSTEM's administration logo (System Branding), read for this screen only; not the company logo
        import base64
        raw, _mime = self.env['ncollection.product.branding']._nc_image('admin')
        image = base64.b64encode(raw)
        for overview in self:
            overview.admin_logo = image

    @api.depends('company_id')
    def _compute_display_name(self):
        for overview in self:
            overview.display_name = self.env._('Overview')

    @api.model
    def _nc_internal_users(self):
        return self.env['res.users'].sudo().with_context(active_test=False).search([
            ('share', '=', False), ('nc_platform_proxy', '=', False)])

    @api.depends('company_id')
    def _compute_figures(self):
        users = self._nc_internal_users()
        owner = self.env.ref(_OWNER_GROUP)
        config = self.env['ncollection.workspace.config'].sudo().get_config()
        roles = self.env['ncollection.role'].sudo().search([], limit=200)
        active = users.filtered('active')
        for overview in self:
            overview.plan_code = config.plan_code or ''
            overview.subscription_status = config.subscription_status or ''
            overview.max_users = config.max_users or 0
            overview.active_users = len(active)
            overview.archived_users = len(users) - len(active)
            overview.administrators = len(active.filtered(lambda user: owner in user.all_group_ids))
            overview.pending_password_changes = len(active.filtered('nc_must_change_password'))
            overview.standard_roles = len(roles.filtered(lambda role: role.kind == 'standard' and role.available))
            overview.custom_roles = len(roles.filtered(lambda role: role.kind == 'custom'))
            overview.has_audit_log = 'ncollection.auth.log' in self.env

    @api.model
    def action_open(self):
        overview = self.create({})
        return {
            'type': 'ir.actions.act_window', 'name': self.env._('Overview'), 'res_model': self._name,
            'res_id': overview.id, 'view_mode': 'form', 'target': 'current',
            'views': [(self.env.ref('ncollection_core.view_workspace_overview_form').id, 'form')],
        }
