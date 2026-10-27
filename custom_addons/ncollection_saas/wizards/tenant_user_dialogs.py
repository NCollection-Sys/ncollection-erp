# -*- coding: utf-8 -*-
"""The dialogs of Tenant Administration: reset a password, change roles, create a user.

Each one re-authorises on the platform when it runs (``tenant._nc_admin_call``), clears a typed password from its row
first, and never records a password anywhere (see models/tenant_admin.py).
"""
from odoo import api, fields, models
from odoo.exceptions import UserError


class TenantUserPassword(models.TransientModel):
    _name = 'ncollection.tenant.user.password'
    _description = 'Reset a tenant user password'

    tenant_id = fields.Many2one('ncollection.tenant', required=True, readonly=True, ondelete='cascade')
    remote_id = fields.Integer(readonly=True)
    user_name = fields.Char(readonly=True, string='User')
    mode = fields.Selection(
        [('temporary', 'Generate a temporary password'), ('explicit', 'Set a password myself')],
        required=True, default='temporary')
    new_password = fields.Char(string='New password')
    must_change = fields.Boolean(string='Must change it at next sign-in', default=True)

    @api.model
    def _nc_open(self, tenant, remote_id, name):
        tenant._nc_admin_authorise()
        wizard = self.create({'tenant_id': tenant.id, 'remote_id': remote_id, 'user_name': name})
        return {
            'type': 'ir.actions.act_window', 'name': self.env._('Reset password'),
            'res_model': self._name, 'res_id': wizard.id, 'view_mode': 'form', 'target': 'new',
        }

    def action_apply(self):
        self.ensure_one()
        tenant, remote_id, name, must_change = self.tenant_id, self.remote_id, self.user_name, self.must_change
        typed = self.new_password if self.mode == 'explicit' else None
        if self.mode == 'explicit' and not typed:
            raise UserError(self.env._("Type the new password, or choose a temporary one."))
        self.new_password = False                          # gone from the row before anything leaves this server
        self.flush_recordset(['new_password'])
        reply = tenant._nc_admin_call('reset_password', {
            'user_id': remote_id, 'password': typed or False, 'must_change': bool(must_change)})
        tenant._nc_admin_note(self.env._(
            "%(admin)s reset the password of %(user)s (%(how)s).", admin=self.env.user.name, user=name,
            how=self.env._('a password chosen by the administrator') if typed else self.env._('a temporary password')))
        temporary = reply.get('temporary_password')
        message = (self.env._(
            "Temporary password for %(user)s: %(password)s  -  give it to them now: it is not shown again.",
            user=name, password=temporary) if temporary
                   else self.env._("The password of %s was changed.", name))
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'title': self.env._("Password reset"), 'message': message,
                       'type': 'warning' if temporary else 'success', 'sticky': bool(temporary),
                       'next': {'type': 'ir.actions.act_window_close'}},
        }


class TenantUserRoles(models.TransientModel):
    _name = 'ncollection.tenant.user.roles'
    _description = 'Change the roles of a tenant user'

    tenant_id = fields.Many2one('ncollection.tenant', required=True, readonly=True, ondelete='cascade')
    remote_id = fields.Integer(readonly=True)
    user_name = fields.Char(readonly=True, string='User')
    role_option_ids = fields.Many2many('ncollection.tenant.role.option', 'nc_tenant_user_roles_opt_rel',
                                       'wizard_id', 'option_id', string='Roles')
    available_role_ids = fields.Many2many('ncollection.tenant.role.option', 'nc_tenant_user_roles_avail_rel',
                                          'wizard_id', 'option_id', string='Roles offered', readonly=True)

    @api.model
    def _nc_open(self, tenant, remote_id, name):
        tenant._nc_admin_authorise()
        options = self.env['ncollection.tenant.role.option']._nc_load(tenant)
        reply = tenant._nc_admin_call('list_users')
        row = next((r for r in reply.get('users', []) if r['id'] == remote_id), None)
        current = options.filtered(lambda option: row and option.code in (row.get('role_codes') or []))
        wizard = self.create({'tenant_id': tenant.id, 'remote_id': remote_id, 'user_name': name,
                              'role_option_ids': [(6, 0, current.ids)], 'available_role_ids': [(6, 0, options.ids)]})
        return {
            'type': 'ir.actions.act_window', 'name': self.env._('Roles of %s', name),
            'res_model': self._name, 'res_id': wizard.id, 'view_mode': 'form', 'target': 'new',
        }

    def action_apply(self):
        self.ensure_one()
        codes = self.role_option_ids.mapped('code')
        self.tenant_id._nc_admin_call('set_roles', {'user_id': self.remote_id, 'role_codes': codes})
        self.tenant_id._nc_admin_note(self.env._(
            "%(admin)s set the roles of %(user)s to: %(roles)s.", admin=self.env.user.name, user=self.user_name,
            roles=', '.join(self.role_option_ids.mapped('name')) or self.env._('none')))
        return self.tenant_id.action_open_users()


class TenantUserCreate(models.TransientModel):
    _name = 'ncollection.tenant.user.create'
    _description = 'Create a tenant user'

    tenant_id = fields.Many2one('ncollection.tenant', required=True, readonly=True, ondelete='cascade')
    name = fields.Char(required=True)
    login = fields.Char(string='Username', required=True,
                        help="What this person types to sign in: a username, never an email address.")
    email = fields.Char(string='Contact email')
    role_option_ids = fields.Many2many('ncollection.tenant.role.option', 'nc_tenant_user_create_opt_rel',
                                       'wizard_id', 'option_id', string='Roles')
    available_role_ids = fields.Many2many('ncollection.tenant.role.option', 'nc_tenant_user_create_avail_rel',
                                          'wizard_id', 'option_id', string='Roles offered', readonly=True)
    mode = fields.Selection(
        [('temporary', 'Generate a temporary password'), ('explicit', 'Set a password myself')],
        required=True, default='temporary')
    new_password = fields.Char(string='Password')
    must_change = fields.Boolean(string='Must change it at first sign-in', default=True)

    @api.model
    def _nc_open(self, tenant):
        tenant._nc_admin_authorise()
        options = self.env['ncollection.tenant.role.option']._nc_load(tenant)
        # Opened with DEFAULTS, never a pre-created record: name and username are required and only the operator can
        # type them, so creating the dialog before it is shown fails with "required value for field name is missing".
        return {
            'type': 'ir.actions.act_window', 'name': self.env._('Add User'),
            'res_model': self._name, 'view_mode': 'form', 'target': 'new',
            'context': {'default_tenant_id': tenant.id, 'default_available_role_ids': [(6, 0, options.ids)]},
        }

    def action_apply(self):
        self.ensure_one()
        tenant, name, login = self.tenant_id, self.name, self.login
        typed = self.new_password if self.mode == 'explicit' else None
        if self.mode == 'explicit' and not typed:
            raise UserError(self.env._("Type the password, or choose a temporary one."))
        codes = self.role_option_ids.mapped('code')
        if not codes:
            raise UserError(self.env._("Choose the role of the new user."))
        self.new_password = False
        self.flush_recordset(['new_password'])
        reply = tenant._nc_admin_call('create_user', {
            'name': name, 'login': login, 'email': self.email or False, 'role_codes': codes,
            'password': typed or False, 'must_change': bool(self.must_change)})
        tenant._nc_admin_note(self.env._(
            "%(admin)s created the user %(login)s.", admin=self.env.user.name, login=login))
        temporary = reply.get('temporary_password')
        message = (self.env._(
            "User %(login)s created. Temporary password: %(password)s  -  give it to them now: it is not shown again.",
            login=login, password=temporary) if temporary
                   else self.env._("User %s created.", login))
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'title': self.env._("User created"), 'message': message,
                       'type': 'warning' if temporary else 'success', 'sticky': bool(temporary),
                       'next': {'type': 'ir.actions.act_window_close'}},
        }


class TenantUserEdit(models.TransientModel):
    _name = 'ncollection.tenant.user.edit'
    _description = 'Edit a tenant user'

    tenant_id = fields.Many2one('ncollection.tenant', required=True, readonly=True, ondelete='cascade')
    remote_id = fields.Integer(readonly=True)
    login = fields.Char(string='Username', readonly=True)
    name = fields.Char(required=True)
    email = fields.Char(string='Contact email')

    @api.model
    def _nc_open(self, tenant, remote_id, name, email):
        tenant._nc_admin_authorise()
        wizard = self.create({'tenant_id': tenant.id, 'remote_id': remote_id, 'name': name, 'email': email or False})
        return {
            'type': 'ir.actions.act_window', 'name': self.env._('Edit user'),
            'res_model': self._name, 'res_id': wizard.id, 'view_mode': 'form', 'target': 'new',
        }

    def action_apply(self):
        self.ensure_one()
        tenant = self.tenant_id
        tenant._nc_admin_call('update_user', {
            'user_id': self.remote_id, 'name': self.name, 'email': self.email or ''})
        tenant._nc_admin_note(self.env._(
            "%(admin)s edited the user %(user)s.", admin=self.env.user.name, user=self.name))
        return tenant.action_open_users()
