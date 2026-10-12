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


from odoo import SUPERUSER_ID, api, fields, models
from odoo.exceptions import AccessDenied, AccessError, ValidationError

from .platform_entry import CREDENTIAL_TYPE

_OWNER_GROUP = 'ncollection_core.group_role_owner'


# Accounts that are addressed by an internal mailbox-style login on purpose.
_SERVICE_LOGIN_SUFFIX = '@ncollection.internal'
# Set ONLY by the (off-by-default) demo signup route, which has no username field.
ALLOW_EMAIL_LOGIN_CTX = 'ncollection_allow_email_login'


class ResUsers(models.Model):
    _inherit = 'res.users'

    nc_platform_proxy = fields.Boolean(
        string='Platform administrator proxy', readonly=True, copy=False, index=True,
        help="Passwordless account a Platform Administrator enters this workspace as. It signs in only "
             "with a one-time grant from the platform (models/platform_entry.py), never with a password.")
    nc_platform_ref = fields.Char(string='Platform administrator ref', readonly=True, copy=False, index=True)

    def _compute_session_token(self, sid):
        """A platform administrator's session ends on its own, ``SESSION_MAX_SECONDS`` after the entry.

        Odoo compares this token on every request, so once the window has passed the token no longer matches
        the one stored in the session and the user is signed out. Removing someone from the platform allow-list
        therefore cannot leave a tenant session open for the cookie's lifetime. Ordinary users are untouched.
        """
        session_hash = super()._compute_session_token(sid)
        if self.nc_platform_proxy and not self.env['ncollection.platform.entry']._session_is_fresh(self):
            return 'expired:' + session_hash
        return session_hash

    def _check_credentials(self, credential, env):
        """A platform proxy signs in ONLY with its own one-time entry grant, and nobody else may present that
        credential type. The grant is verified and consumed atomically in ``ncollection.platform.entry``.

        MFA is NOT skipped (``'mfa': 'default'``): whatever second-factor policy this workspace has still
        applies. The platform administrator's own strong authentication happened on the platform.
        """
        if credential.get('type') == CREDENTIAL_TYPE:
            self.ensure_one()
            user = self
            self.env['ncollection.platform.entry']._redeem(
                user, credential.get('token'), (env or {}).get('REMOTE_ADDR', ''))
            return {'uid': user.id, 'auth_method': CREDENTIAL_TYPE, 'mfa': 'default'}
        if self.nc_platform_proxy:
            raise AccessDenied()                  # no password path for a proxy, whatever is stored on it
        return super()._check_credentials(credential, env)

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
        self._nc_refuse_credentials_from_proxy(vals_list)
        if not self.env.su and any(self._nc_proxy_keys(vals) for vals in vals_list):
            raise AccessError(self.env._("Platform administrator accounts are managed by the platform."))
        users = super().create(vals_list)
        if self.env.su:
            return users  # platform/provisioning/install flows are exempt
        new_internal = users.filtered(lambda u: u.active and not u.share)
        if new_internal:
            self._ncollection_check_user_limit()
        return users

    @api.model
    def _nc_actor_is_platform_admin(self):
        """True when the user making this call is an NCollection PLATFORM administrator: a member of the platform
        administrator group AND a system administrator, in a database where that group exists (the platform's own).

        The group lives in ``ncollection_subscription``, which is never installed in a tenant database, so in a tenant
        this is always False: a tenant Owner (who implies base.group_system) can neither be one nor create the group.
        It is the acting user that counts (``sudo()`` keeps it), never a display name or a client-sent flag.
        """
        user = self.env.user
        group = self.env.ref('ncollection_subscription.group_platform_admin', raise_if_not_found=False)
        # base.group_system implies the platform group wherever it exists, so the tenant Owner ROLE (which also
        # implies base.group_system, and which platform staff never hold) is what tells the two contexts apart.
        return bool(group and self.env.uid != SUPERUSER_ID and user.has_group('base.group_system')
                    and user.has_group('ncollection_subscription.group_platform_admin')
                    and not user.has_group('ncollection_core.group_role_owner'))

    @api.model
    def _nc_actor_is_platform_proxy(self):
        """True when the user making this call is a platform administrator's entry proxy. ``sudo()`` does not change
        the acting user, so it cannot be used to step around a check built on this."""
        return bool(self.env.uid != SUPERUSER_ID and self.env.user.sudo().nc_platform_proxy)

    def _nc_refuse_credentials_from_proxy(self, vals_list):
        """A platform proxy session may not set any user's password: that would be a persistent credential (for
        itself, or for a real account) that outlives the session. Real tenant users keep every right they have."""
        if self._nc_actor_is_platform_proxy() and any('password' in vals for vals in vals_list):
            raise AccessError(self.env._(
                "A platform administrator session cannot set passwords. Ask the workspace Owner."))

    @staticmethod
    def _nc_proxy_keys(vals):
        return {'nc_platform_proxy', 'nc_platform_ref'} & set(vals)

    def write(self, vals):
        self._nc_refuse_credentials_from_proxy([vals])
        # `readonly=True` is only a UI hint: a tenant admin must not be able to mark a real user as a proxy (it would
        # leave the seat count) or create/alter a proxy by hand. Only the entry flow (superuser env) may.
        if self._nc_proxy_keys(vals) and not self.env.su:
            raise AccessError(self.env._("Platform administrator accounts are managed by the platform."))
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
            ('nc_platform_proxy', '=', False),   # a platform administrator's proxy is not a customer seat
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
