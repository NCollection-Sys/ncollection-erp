# -*- coding: utf-8 -*-
"""Workspace user administration on ``res.users`` (roles, activation, password reset), ONE implementation.

The Workspace Administration screens and the platform's Tenant Administration (through
``ncollection.admin.service``) both end up in the methods below, so a rule such as "the last Administrator cannot
be removed" or "a reset is audited" exists once. Whether the caller may use them is decided by the caller's own
rights (the ORM ACL on ``res.users``, plus the guards here), never by who built the screen.

Roles (``ncollection.role``) are a VIEW of the groups a user holds, not a second store: ``nc_role_ids`` is computed
from ``all_group_ids`` and its inverse adds / removes real groups, so a role change takes effect through Odoo's own
access control and cannot drift from it.

Passwords: a reset sets the password through the ORM (hashed by Odoo, policy checked), marks the account "must
change on next sign-in" and returns a generated one-time password to the CALLER only. The password is never written to
a log, an audit row, a chatter message or any record of ours; the audit says a reset happened, by whom and to whom.
"""
import re
import secrets

from odoo import SUPERUSER_ID, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .role import ADMIN_GROUP

_OWNER_GROUP = 'ncollection_core.group_role_owner'
USERNAME_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{2,63}')
# no look-alikes (0 O 1 l I)
_LOWER, _UPPER, _DIGIT = 'abcdefghjkmnpqrstuvwxyz', 'ABCDEFGHJKLMNPQRSTUVWXYZ', '23456789'
_SYMBOL = '-_.@#'
TEMP_PASSWORD_LENGTH = 16
MIN_EXPLICIT_PASSWORD = 10


def generate_password(length=TEMP_PASSWORD_LENGTH):
    """A random password from the system CSPRNG with at least one of each class."""
    chars = [secrets.choice(_LOWER), secrets.choice(_UPPER), secrets.choice(_DIGIT), secrets.choice(_SYMBOL)]
    alphabet = _LOWER + _UPPER + _DIGIT + _SYMBOL
    chars += [secrets.choice(alphabet) for _i in range(length - len(chars))]
    secrets.SystemRandom().shuffle(chars)
    return ''.join(chars)


class ResUsers(models.Model):
    _inherit = 'res.users'

    nc_role_ids = fields.Many2many(
        'ncollection.role', string='Roles', compute='_compute_nc_role_ids', inverse='_inverse_nc_role_ids',
        compute_sudo=True, help="The workspace roles this user holds. Derived from the user's access groups.")
    nc_role_summary = fields.Char(string='Role', compute='_compute_nc_role_ids', compute_sudo=True)
    nc_must_change_password = fields.Boolean(
        string='Must change password', copy=False, readonly=True,
        help="Set by a password reset: the next sign-in is sent to a page that makes the person choose their own "
             "password before anything else opens.")

    # ------------------------------------------------------------------ roles as a view of the groups
    def _nc_held_roles(self):
        """The roles whose groups this user holds in full (a role needing an application that is absent is skipped)."""
        self.ensure_one()
        held = self.env['ncollection.role']
        if self.share:
            return held
        mine = self.sudo().all_group_ids
        for role in self.env['ncollection.role'].sudo().search([], limit=200):
            if role.available and role.group_ids <= mine:
                held |= role
        return held

    @api.model
    def _nc_primary_roles(self, held):
        """``held`` without the roles another held role already includes (Sales User beside Sales Manager)."""
        primary = self.env['ncollection.role']
        for role in held:
            mine = role.group_ids
            covered = any(
                other != role and mine <= other.group_ids.all_implied_ids
                and not (other.group_ids.all_implied_ids <= mine.all_implied_ids and other.sequence > role.sequence)
                for other in held)
            if not covered:
                primary |= role
        return primary

    @api.depends('group_ids', 'all_group_ids')
    def _compute_nc_role_ids(self):
        for user in self:
            primary = self._nc_primary_roles(user._nc_held_roles()) if user.id else self.env['ncollection.role']
            user.nc_role_ids = primary
            user.nc_role_summary = ', '.join(primary.mapped('name'))

    def _inverse_nc_role_ids(self):
        for user in self:
            user._nc_apply_roles(user.nc_role_ids)

    def nc_set_roles(self, role_ids):
        """Make ``role_ids`` the roles of these users (public: the workspace user form and the platform service)."""
        roles = self.env['ncollection.role'].browse(role_ids).exists()
        for user in self:
            user._nc_apply_roles(roles)
        return True

    def _nc_apply_roles(self, wanted):
        """Add the groups of the roles added, remove those only a removed role justified. Real groups, nothing else."""
        self.ensure_one()
        if self.share or self.nc_platform_proxy:
            raise UserError(self.env._("Roles apply to the workspace's own users only."))
        Role = self.env['ncollection.role'].sudo()
        self._nc_check_role_change_allowed(Role.browse())          # who may touch roles at all, even for a no-op
        wanted = Role.browse(wanted.ids)
        held = self._nc_held_roles()
        current = self._nc_primary_roles(held)
        added, removed = wanted - held, current - wanted
        if not added and not removed:
            return
        unavailable = added.filtered(lambda role: not role.available)
        if unavailable:
            raise UserError(self.env._(
                "%(roles)s cannot be given here: the application it needs is not part of this workspace (%(apps)s).",
                roles=', '.join(unavailable.mapped('name')),
                apps=', '.join(filter(None, unavailable.mapped('missing_apps')))))
        self._nc_check_role_change_allowed(added | removed)
        keep = self.env['res.groups']
        for role in wanted:
            keep |= role.group_ids
        keep_closure = keep | keep.all_implied_ids
        to_add = self.env['res.groups']
        for role in added:
            to_add |= role.group_ids
        to_remove = self.env['res.groups']
        for role in removed:
            to_remove |= role.group_ids - keep_closure
        if removed and not wanted.filtered(lambda role: role.grants_administration) \
                and any(role.grants_administration for role in removed):
            self._nc_check_not_last_administrator()
        self.sudo().write({'group_ids': [(4, group.id) for group in to_add] + [(3, group.id) for group in to_remove]})
        self._nc_audit(
            'admin_roles_change', "added: %s; removed: %s" % (
                ', '.join(added.mapped('code')) or '-', ', '.join(removed.mapped('code')) or '-'))

    def _nc_check_role_change_allowed(self, roles):
        """Only an administrator hands out (or takes away) administration, and nobody hands out a group they lack."""
        actor = self.env.user
        if self.env.su and self.env.uid == SUPERUSER_ID:
            return                                          # the platform service path authorised the caller itself
        if actor.has_group(ADMIN_GROUP):
            return
        if not actor.has_group('base.group_erp_manager'):
            raise AccessError(self.env._("You are not allowed to change roles."))
        if any(role.grants_administration for role in roles) or self.sudo().has_group(ADMIN_GROUP):
            raise AccessError(self.env._("Only a workspace administrator can change administrator roles."))
        missing = self.env['res.groups']
        for role in roles:
            missing |= role.group_ids - actor.all_group_ids
        if missing:
            raise AccessError(self.env._("You cannot give a role that carries rights you do not have yourself."))

    def _nc_check_not_last_administrator(self):
        owner = self.env.ref(_OWNER_GROUP)
        others = self.sudo().search_count([
            ('id', 'not in', self.ids), ('active', '=', True), ('share', '=', False),
            ('nc_platform_proxy', '=', False), ('all_group_ids', 'in', owner.id)])
        if not others:
            raise ValidationError(self.env._("You cannot remove the last workspace Administrator."))

    # ------------------------------------------------------------------ activation
    def nc_set_active(self, active):
        """Activate or deactivate. Deactivation keeps every guard of ``action_ncollection_deactivate`` (not yourself,
        not the last Owner); activation is limited by the plan's user count like any new user."""
        for user in self:
            if user.share or user.nc_platform_proxy:
                raise UserError(self.env._("Only the workspace's own users can be activated or deactivated here."))
        if active:
            if not (self.env.su and self.env.uid == SUPERUSER_ID) and not self.env.user.has_group(ADMIN_GROUP):
                raise AccessError(self.env._("Only a workspace administrator can activate users."))
            for user in self:
                if user.login == '__system__' or user.login.endswith('@ncollection.internal'):
                    raise UserError(self.env._("That account is not managed here."))
            self.sudo().write({'active': True})
            self.env['res.users']._ncollection_check_user_limit()
            self._nc_audit('admin_user_activate', 'activated')
            return True
        if self.env.su and self.env.uid == SUPERUSER_ID:
            owner = self.env.ref(_OWNER_GROUP)
            for user in self:
                if owner in user.all_group_ids:
                    user._nc_check_not_last_administrator()
            self.sudo().write({'active': False})
        else:
            self.action_ncollection_deactivate()
        self._nc_audit('admin_user_deactivate', 'deactivated')
        return True

    # ------------------------------------------------------------------ profile
    def nc_update_profile(self, name=None, email=None):
        """Change the name and / or the contact email of ONE workspace user (never the login, never the groups)."""
        self.ensure_one()
        self._nc_check_password_target()                    # the same rule as a reset: who may act on this account
        changes = {}
        if name is not None:
            if not name.strip():
                raise UserError(self.env._("A user needs a name."))
            changes['name'] = name.strip()[:128]
        if email is not None:
            email = email.strip()
            if email and '@' not in email:
                raise UserError(self.env._("Please enter a valid email address."))
            changes['email'] = email or False
        if changes:
            self.sudo().write(changes)
            self._nc_audit('admin_user_update', 'updated: %s' % ', '.join(sorted(changes)))
        return True

    # ------------------------------------------------------------------ password reset
    def nc_reset_password(self, new_password=None, must_change=True):
        """Set a new password for this ONE user and return it when it was generated, so the caller can hand it over.

        ``new_password`` None  -> a temporary password is generated and returned (this is the only time it exists in
        clear). Otherwise the explicit password is used (policy checked) and nothing is returned.
        """
        self.ensure_one()
        self._nc_check_password_target()
        if new_password is not None and not isinstance(new_password, str):
            raise UserError(self.env._("Malformed request."))
        explicit = bool(new_password)
        if explicit:
            if len(new_password) < MIN_EXPLICIT_PASSWORD:
                raise UserError(self.env._("A password needs at least %s characters.", MIN_EXPLICIT_PASSWORD))
            password = new_password
        else:
            password = generate_password()
        self.sudo().write({'password': password, 'nc_must_change_password': bool(must_change)})
        # an API key issued before the reset must not outlive it
        self.env['res.users.apikeys'].sudo().search([('user_id', 'in', self.ids)]).unlink()
        self._nc_audit('admin_password_reset', 'set by administrator (%s, %s)' % (
            'chosen' if explicit else 'temporary', 'change required' if must_change else 'no forced change'))
        return None if explicit else password

    def _nc_check_password_target(self):
        actor = self.env.user
        for user in self:
            if user.share or user.nc_platform_proxy:
                raise UserError(self.env._("Passwords are managed here for the workspace's own users only."))
            if self.env.su and self.env.uid == SUPERUSER_ID:
                continue
            if not actor.has_group(ADMIN_GROUP):
                if (not actor.has_group('base.group_erp_manager') or user.sudo().has_group(ADMIN_GROUP)
                        or user.sudo().all_group_ids - actor.all_group_ids):
                    raise AccessError(self.env._("Only a workspace administrator can reset this password."))

    def _nc_clear_must_change(self):
        """The person has chosen their own password: lift the flag (called by the change-password page only)."""
        self.sudo().write({'nc_must_change_password': False})

    # ------------------------------------------------------------------ buttons of the workspace screens
    def action_nc_open_reset_password(self):
        self.ensure_one()
        self._nc_check_password_target()
        wizard = self.env['ncollection.user.reset.password'].create({'user_id': self.id})
        return {
            'type': 'ir.actions.act_window', 'name': self.env._('Reset password'),
            'res_model': wizard._name, 'res_id': wizard.id, 'view_mode': 'form', 'target': 'new',
        }

    def action_nc_activate(self):
        return self.nc_set_active(True)

    # ------------------------------------------------------------------ audit
    def _nc_audit(self, event, detail):
        log = self.env.get('ncollection.auth.log')
        if log is None:
            return
        elevated = self.env.uid == SUPERUSER_ID           # only code can be here; a client-sent context cannot
        actor = None if elevated else self.env.user
        actor_ref = self.env.context.get('nc_platform_actor') if elevated else None
        for user in self:
            log._capture_admin(event, actor=actor, target=user, detail=detail, actor_ref=actor_ref)

    # ------------------------------------------------------------------ creation
    @api.model
    def _nc_create_workspace_user(self, name, login, email=None, role_ids=(), password=None, must_change=True):
        """Create an internal user with the given roles in THIS workspace; returns (user, generated_password|None).

        The plan's seat limit is checked here explicitly, because a superuser call (the platform service) is exempt
        in ``create`` and must not become a way around the plan.
        """
        name, login = (name or '').strip(), (login or '').strip()
        if not name or not login:
            raise UserError(self.env._("A user needs a name and a username."))
        if not USERNAME_RE.fullmatch(login):
            raise UserError(self.env._(
                "The username cannot be an email address. Use 3-64 letters, digits, dots, dashes or underscores, "
                "such as sara.m."))
        if self.sudo().with_context(active_test=False).search_count([('login', '=', login)]):
            raise ValidationError(self.env._("The username '%s' is already taken.", login))
        roles = self.env['ncollection.role'].sudo().browse(role_ids).exists()
        if password is not None and not isinstance(password, str):
            raise UserError(self.env._("Malformed request."))
        explicit = bool(password)
        # A platform administrator's entry proxy adds users exactly as the platform's Users & Access does: a generated
        # temporary password the person must change at first sign-in, never one the proxy chose (that would be a
        # credential the platform knows and that outlives the session - the rule of _nc_refuse_credentials_from_proxy).
        proxy = self._nc_actor_is_platform_proxy()
        if proxy and explicit:
            raise AccessError(self.env._(
                "A platform administrator session cannot set passwords. Leave the temporary password: the person "
                "sets their own at first sign-in."))
        if explicit and len(password) < MIN_EXPLICIT_PASSWORD:
            raise UserError(self.env._("A password needs at least %s characters.", MIN_EXPLICIT_PASSWORD))
        secret = password or generate_password()
        vals = {
            'name': name, 'login': login, 'email': email or False,
            'group_ids': [(4, self.env.ref('base.group_user').id)],
            'nc_must_change_password': True if proxy else bool(must_change),
        }
        if proxy:
            user = self.create(vals)
            # The one password write a proxy causes: generated here, server side, and forced to change.
            user.with_user(SUPERUSER_ID).write({'password': secret})
        else:
            user = self.create(dict(vals, password=secret))
        self.env['res.users']._ncollection_check_user_limit()
        if roles:
            user._nc_apply_roles(roles)
        user._nc_audit('admin_user_create', 'created with roles: %s' % (', '.join(roles.mapped('code')) or '-'))
        return user, (None if explicit else secret)
