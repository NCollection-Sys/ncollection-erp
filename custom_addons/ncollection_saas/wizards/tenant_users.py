# -*- coding: utf-8 -*-
"""The tenant's user list as a snapshot read from the tenant database (see models/tenant_admin.py)."""
from odoo import api, fields, models


class TenantUsers(models.TransientModel):
    """A snapshot of a tenant's users, read from the tenant database when the screen was opened. Holds no secret."""
    _name = 'ncollection.tenant.users'
    _description = 'Tenant users (snapshot)'

    tenant_id = fields.Many2one('ncollection.tenant', required=True, readonly=True, ondelete='cascade')
    max_users = fields.Integer(readonly=True)
    active_internal = fields.Integer(readonly=True)
    line_ids = fields.One2many('ncollection.tenant.user', 'users_id', readonly=True)

    @api.depends('tenant_id')
    def _compute_display_name(self):
        for users in self:
            users.display_name = self.env._('Users of %s', users.tenant_id.company_name or '')

    def action_refresh(self):
        self.ensure_one()
        return self.tenant_id.action_open_users()

    def action_create_user(self):
        self.ensure_one()
        return self.env['ncollection.tenant.user.create']._nc_open(self.tenant_id)


class TenantUser(models.TransientModel):
    _name = 'ncollection.tenant.user'
    _description = 'Tenant user (snapshot row)'
    _order = 'active desc, name'

    users_id = fields.Many2one('ncollection.tenant.users', ondelete='cascade', required=True)
    tenant_id = fields.Many2one(related='users_id.tenant_id')
    remote_id = fields.Integer(readonly=True)
    name = fields.Char(readonly=True)
    login = fields.Char(string='Username', readonly=True)
    email = fields.Char(readonly=True)
    active = fields.Boolean(readonly=True)
    administrator = fields.Boolean(string='Administrator level', readonly=True)
    is_tenant_admin = fields.Boolean(string='Tenant Administrator', readonly=True)
    role_names = fields.Char(string='Role', readonly=True)
    must_change_password = fields.Boolean(readonly=True)
    last_login = fields.Char(string='Last sign-in', readonly=True)

    def action_reset_password(self):
        self.ensure_one()
        return self.env['ncollection.tenant.user.password']._nc_open(self.tenant_id, self.remote_id, self.name)

    def action_toggle_active(self):
        self.ensure_one()
        tenant = self.tenant_id
        wanted = not self.active
        tenant._nc_admin_call('set_active', {'user_id': self.remote_id, 'active': wanted})
        tenant._nc_admin_note(self.env._("%(admin)s %(what)s the user %(login)s.", admin=self.env.user.name,
                                         what=self.env._('activated') if wanted else self.env._('deactivated'),
                                         login=self.login))
        return tenant.action_open_users()

    def action_edit(self):
        self.ensure_one()
        return self.env['ncollection.tenant.user.edit']._nc_open(
            self.tenant_id, self.remote_id, self.name, self.email)

    def action_edit_roles(self):
        self.ensure_one()
        return self.env['ncollection.tenant.user.roles']._nc_open(self.tenant_id, self.remote_id, self.name)


class TenantRoleOption(models.TransientModel):
    """A role the tenant offers, as read from the tenant (the platform keeps no role catalogue of its own)."""
    _name = 'ncollection.tenant.role.option'
    _description = 'Tenant role option (snapshot)'
    _order = 'sequence, name'

    code = fields.Char(readonly=True)
    name = fields.Char(readonly=True)
    available = fields.Boolean(readonly=True)
    administrator = fields.Boolean(readonly=True)
    sequence = fields.Integer(readonly=True)

    @api.model
    def _nc_load(self, tenant):
        reply = tenant._nc_admin_call('list_roles')
        return self.create([{
            'code': row['code'], 'name': row['name'], 'available': bool(row.get('available')),
            'administrator': bool(row.get('administrator')), 'sequence': index,
        } for index, row in enumerate(reply.get('roles', [])) if row.get('available')])
