# -*- coding: utf-8 -*-
"""Owner "Invite User" wizard (P1-T12).

The tenant Owner's replacement for raw user administration: name + email +
ONE of the 8 predefined NCollection roles (P1-T08). Raw res.groups is never
exposed — the role selection maps to group xml-ids internally.

Access: ACL grants this wizard to group_role_owner only, so a non-Owner is
denied at the ORM even if they discover the action (Rule 4 mirror of the
Owner-only menu). The max-user limit is enforced BOTH here (friendly
pre-flight) and in res.users.create (the real control).
"""

import logging

from odoo import api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

# role key -> (label, group xml-id). Single source: ROLE_MATRIX.md roles.
ROLE_GROUPS = {
    'employee': ('Employee', 'ncollection_core.group_role_employee'),
    'sales': ('Sales', 'ncollection_core.group_role_sales'),
    'warehouse': ('Warehouse', 'ncollection_core.group_role_warehouse'),
    'hr': ('HR', 'ncollection_core.group_role_hr'),
    'accountant': ('Accountant', 'ncollection_core.group_role_accountant'),
    'manager': ('Manager', 'ncollection_core.group_role_manager'),
    'ceo': ('CEO', 'ncollection_core.group_role_ceo'),
    'owner': ('Owner', 'ncollection_core.group_role_owner'),
}


class UserInvite(models.TransientModel):
    _name = 'ncollection.user.invite'
    _description = 'NCollection Invite Workspace User'

    name = fields.Char(string='Full Name', required=True)
    email = fields.Char(required=True)
    role = fields.Selection(
        selection=[(key, label) for key, (label, _xmlid) in ROLE_GROUPS.items()],
        required=True,
        default='employee',
        help='One of the 8 predefined NCollection workspace roles '
             '(see docs/ROLE_MATRIX.md).',
    )

    @api.constrains('email')
    def _check_email(self):
        for wizard in self:
            if '@' not in (wizard.email or ''):
                raise ValidationError(self.env._('Please enter a valid email address.'))

    def action_invite(self):
        self.ensure_one()
        Users = self.env['res.users']

        # Friendly pre-flight (the hard control lives in res.users.create).
        max_users = self.env['ncollection.workspace.config'].get_max_users()
        if max_users:
            active_internal = Users.sudo().search_count([
                ('active', '=', True), ('share', '=', False),
            ])
            if active_internal >= max_users:
                raise ValidationError(self.env._(
                    'Your plan allows a maximum of %(limit)s users. Upgrade '
                    'your subscription to invite more.',
                    limit=max_users,
                ))

        role_group = self.env.ref(ROLE_GROUPS[self.role][1])
        user = Users.create({
            'name': self.name,
            'login': self.email,
            'email': self.email,
            'group_ids': [
                (4, self.env.ref('base.group_user').id),
                (4, role_group.id),
            ],
        })

        # Best-effort invitation email (signup/reset link). Dev environments
        # have no SMTP — never fail the invite over mail transport.
        try:
            if hasattr(user, 'action_reset_password'):
                user.with_context(create_user=True).action_reset_password()
        except Exception:  # pragma: no cover - depends on mail infra
            _logger.info(
                "Invitation email for %s could not be sent (no outgoing "
                "mail server?); the user was created.", user.login,
            )

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': self.env._('User invited'),
                'message': self.env._(
                    '%(name)s was added with the %(role)s role.',
                    name=user.name, role=ROLE_GROUPS[self.role][0],
                ),
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
