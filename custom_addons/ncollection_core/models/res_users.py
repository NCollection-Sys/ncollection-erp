# -*- coding: utf-8 -*-
"""Owner workspace user management (P1-T12).

Two ORM-layer controls (Rule 4 — the UI mirrors these, never replaces them):

1. Max-user enforcement: creating an ACTIVE INTERNAL user beyond the plan's
   ``max_users`` (ncollection.workspace.config) raises a friendly upgrade
   ValidationError. Superuser/su flows (provisioning, platform sync, module
   installs) are exempt. 0/unset = unlimited (fail-open).

2. Guarded deactivation: ``action_ncollection_deactivate`` is the ONLY
   surface the workspace UI exposes for disabling users. Owner-only at the
   ORM (AccessError over any path, RPC included), with self-deactivation and
   last-Owner protection.
"""


from odoo import api, models
from odoo.exceptions import AccessError, ValidationError

_OWNER_GROUP = 'ncollection_core.group_role_owner'


# Accounts that are addressed by an internal mailbox-style login on purpose.
_SERVICE_LOGIN_SUFFIX = '@ncollection.internal'
# Set ONLY by the (off-by-default) demo signup route, which has no username field.
ALLOW_EMAIL_LOGIN_CTX = 'ncollection_allow_email_login'


class ResUsers(models.Model):
    _inherit = 'res.users'

    @api.constrains('login')
    def _check_login_is_a_username(self):
        """The login of a person who runs the ERP is a username, never an email.

        An email is a contact detail; welding it to the credential made the
        two inseparable. Customers (portal/public users, who sign in with their
        email), the platform service accounts and any login that existed before
        this rule are untouched: a constraint only fires when the login is set
        or changed.
        """
        if self.env.context.get(ALLOW_EMAIL_LOGIN_CTX):
            return
        for user in self:
            login = user.login or ''
            if '@' not in login or user.share:
                continue
            if login.endswith(_SERVICE_LOGIN_SUFFIX) or login == '__system__':
                continue
            raise ValidationError(self.env._(
                "The login '%s' looks like an email address. A login is a "
                "username (for example sara.m); put the email in the contact "
                "email field instead.", login))

    # ------------------------------------------------------------------
    # Max-user enforcement (plan limit)
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        users = super().create(vals_list)
        if self.env.su:
            return users  # platform/provisioning/install flows are exempt
        new_internal = users.filtered(lambda u: u.active and not u.share)
        if new_internal:
            self._ncollection_check_user_limit()
        return users

    def write(self, vals):
        result = super().write(vals)
        # Re-activating an archived user also consumes a seat.
        if not self.env.su and vals.get('active'):
            if any(not u.share for u in self):
                self._ncollection_check_user_limit()
        return result

    @api.model
    def _ncollection_check_user_limit(self):
        max_users = self.env['ncollection.workspace.config'].get_max_users()
        if not max_users:
            return  # unlimited
        active_internal = self.sudo().search_count([
            ('active', '=', True), ('share', '=', False),
        ])
        if active_internal > max_users:
            raise ValidationError(self.env._(
                'Your plan allows a maximum of %(limit)s users and you '
                'already have %(count)s. Upgrade your subscription to add '
                'more users.',
                limit=max_users, count=active_internal,
            ))

    # ------------------------------------------------------------------
    # Guarded deactivation (workspace UI surface)
    # ------------------------------------------------------------------
    def action_ncollection_deactivate(self):
        actor = self.env.user
        if not (self.env.su or actor._is_superuser() or actor.has_group(_OWNER_GROUP)):
            raise AccessError(self.env._(
                'Only the workspace Owner can deactivate users.'
            ))
        owner_group = self.env.ref(_OWNER_GROUP)
        for user in self:
            if user == actor:
                raise ValidationError(self.env._(
                    'You cannot deactivate your own account.'
                ))
            if owner_group in user.all_group_ids:
                other_owners = self.sudo().search_count([
                    ('id', '!=', user.id), ('active', '=', True),
                    ('share', '=', False),
                    ('all_group_ids', 'in', owner_group.id),
                ])
                if not other_owners:
                    raise ValidationError(self.env._(
                        'You cannot deactivate the last workspace Owner.'
                    ))
        self.sudo().write({'active': False})
