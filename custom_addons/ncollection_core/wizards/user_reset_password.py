# -*- coding: utf-8 -*-
"""Workspace administrator's "Reset password" dialog (the screen over ``res.users.nc_reset_password``).

Two ways, chosen by the administrator: a generated TEMPORARY password (shown once, in the confirmation, to the
administrator only) or a password they type. Either way the person must choose their own on the next sign-in unless the
administrator turns that off. The typed password lives in this transient row only until the button is pressed, is
cleared before anything else runs (like Odoo's own Change Password dialog) and is never logged or audited.
"""
from odoo import fields, models
from odoo.exceptions import UserError


class UserResetPassword(models.TransientModel):
    _name = 'ncollection.user.reset.password'
    _description = 'Reset a workspace user password'

    user_id = fields.Many2one('res.users', required=True, readonly=True, ondelete='cascade',
                              domain=[('share', '=', False)])
    mode = fields.Selection(
        [('temporary', 'Generate a temporary password'), ('explicit', 'Set a password myself')],
        required=True, default='temporary')
    new_password = fields.Char(string='New password')
    must_change = fields.Boolean(string='Must change it at next sign-in', default=True)

    def action_apply(self):
        self.ensure_one()
        user, must_change = self.user_id, self.must_change
        typed = self.new_password if self.mode == 'explicit' else None
        if self.mode == 'explicit' and not typed:
            raise UserError(self.env._("Type the new password, or choose a temporary one."))
        self.new_password = False                  # gone from the row before the reset runs
        self.flush_recordset(['new_password'])
        temporary = user.nc_reset_password(typed, must_change=must_change)
        if temporary:
            message = self.env._(
                "Temporary password for %(user)s: %(password)s  -  give it to them now: it is not shown again.",
                user=user.name, password=temporary)
        else:
            message = self.env._("The password of %s was changed.", user.name)
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'title': self.env._("Password reset"), 'message': message,
                       'type': 'warning' if temporary else 'success', 'sticky': bool(temporary),
                       'next': {'type': 'ir.actions.act_window_close'}},
        }
