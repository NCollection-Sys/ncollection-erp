# -*- coding: utf-8 -*-
"""Workspace administration "Add User" (was "Invite User", P1-T12).

The tenant administrator adds a user DIRECTLY, ready to sign in: name, username (never an email address), contact
email and the role(s) from Roles & Permissions (``ncollection.role``, real groups). An invitation e-mail could not
make the user usable (a workspace may have no outgoing mail server, and the link expires), so the user gets a
password now: a generated temporary one shown once to the administrator, or one the administrator types, which the
person must change at the next sign-in unless that is turned off - the same rule as Reset password.

Everything goes through the one creation service, ``res.users._nc_create_workspace_user`` (the platform's Tenant
Administration uses it too): username rule, seat limit, roles applied by ``_nc_apply_roles`` (only an administrator
hands out administration, nobody hands out a group they lack, a role whose application is not part of the workspace
is refused) and the administrative audit. The wizard adds no rule of its own.

Access: the ACL grants this wizard to the workspace Owner only (Rule 4 mirror of the Owner-only menu). The model keeps
its technical name, ``ncollection.user.invite``.
"""
from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..models.res_users_admin import USERNAME_RE


class UserInvite(models.TransientModel):
    _name = 'ncollection.user.invite'
    _description = 'New workspace user'

    name = fields.Char(string='Full Name', required=True)
    username = fields.Char(
        required=True,
        help='What this person types to sign in. A username, never an email address - their email is only a contact '
             'detail.')
    email = fields.Char(string='Contact Email', required=True)
    available_role_ids = fields.Many2many('ncollection.role', compute='_compute_available_role_ids')
    role_id = fields.Many2one(
        'ncollection.role', string='Role', required=True, ondelete='cascade',
        domain="[('id', 'in', available_role_ids)]",
        help='What this person may do in the workspace (Roles & Permissions).')
    role_ids = fields.Many2many(
        'ncollection.role', 'ncollection_user_invite_role_rel', 'invite_id', 'role_id', string='More roles',
        domain="[('id', 'in', available_role_ids), ('id', '!=', role_id)]",
        help='Workspace roles given in addition to the role above.')
    mode = fields.Selection(
        [('temporary', 'Generate a temporary password'), ('explicit', 'Set a password myself')],
        string='Password', required=True, default='temporary')
    new_password = fields.Char(string='New password')
    must_change = fields.Boolean(string='Must change it at first sign-in', default=True)

    @api.depends_context('uid')
    def _compute_available_role_ids(self):
        roles = self.env['ncollection.role'].search([]).filtered('available')
        for wizard in self:
            wizard.available_role_ids = roles

    @api.constrains('username')
    def _check_username(self):
        for wizard in self:
            if not USERNAME_RE.fullmatch(wizard.username or ''):
                raise ValidationError(self.env._(
                    'The username cannot be an email address. Use 3-64 letters, '
                    'digits, dots, dashes or underscores, such as sara.m.'))

    @api.constrains('email')
    def _check_email(self):
        for wizard in self:
            if '@' not in (wizard.email or ''):
                raise ValidationError(self.env._('Please enter a valid email address.'))

    def action_create_user(self):
        self.ensure_one()
        typed = self.new_password if self.mode == 'explicit' else None
        if self.mode == 'explicit' and not typed:
            raise UserError(self.env._("Type the password, or choose a temporary one."))
        self.new_password = False                  # gone from the row before anything else runs (never logged)
        self.flush_recordset(['new_password'])
        user, temporary = self.env['res.users']._nc_create_workspace_user(
            self.name, self.username, email=self.email, role_ids=(self.role_id | self.role_ids).ids,
            password=typed, must_change=self.must_change)
        if temporary:
            message = self.env._(
                "%(user)s can sign in now as %(login)s with the temporary password %(password)s  -  give it to them "
                "now: it is not shown again.", user=user.name, login=user.login, password=temporary)
        else:
            message = self.env._("%(user)s can sign in now as %(login)s.", user=user.name, login=user.login)
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'title': self.env._("User created"), 'message': message,
                       'type': 'warning' if temporary else 'success', 'sticky': bool(temporary),
                       'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}},
        }
