# -*- coding: utf-8 -*-
"""The platform's door into a workspace's user administration (tenant side of "Tenant Administration").

A Platform Administrator manages a tenant's users from the platform. Database-per-tenant means the platform never
touches tenant tables: like config-sync and Enter Company it calls this tenant over JSON-2 with the tenant's own
derived bearer (the config-sync service account, ``group_config_sync``), and this model performs the work in THIS
database through the same ``res.users`` methods the Workspace Administration screens use (models/res_users_admin.py).

Why a service model and not more rights for the service account: that account is deliberately limited to the workspace
configuration. Granting it user-administration ACLs would make a leaked bearer a skeleton key. Instead the account
may call exactly the methods below, each of which

  * refuses anyone but the service account (``_service_only``),
  * accepts a fixed set of keys and shapes (no free-form write, no group ids, no field names),
  * refuses the accounts the platform itself relies on (service, cron, proxy, portal),
  * then runs under ``sudo()`` for that ONE operation, the legitimate platform -> tenant elevation: the same
    guards (last Administrator, seat limit, forbidden groups) still apply because they live in ``res.users`` / the
    role model, not in the caller,
  * records who asked: the platform administrator's reference and name travel as non-secret metadata and are written
    to the tenant's audit log (``ncollection.auth.log``) next to the operation itself.

Passwords: a generated password is returned in the reply to the caller and nowhere else; an explicit one is applied and
never echoed. Neither is logged or stored by this model.
"""
import re

from odoo import SUPERUSER_ID, api, models
from odoo.exceptions import AccessError, UserError

_REF_RE = re.compile(r'^u[0-9]{1,23}$')
_CODE_RE = re.compile(r'^[a-z][a-z0-9_]{1,63}$')
_SERVICE_LOGIN_SUFFIX = '@ncollection.internal'
_KEYS = {
    'list_users': {'actor'},
    'list_roles': {'actor'},
    'create_user': {'actor', 'name', 'login', 'email', 'role_codes', 'password', 'must_change'},
    'set_active': {'actor', 'user_id', 'active'},
    'reset_password': {'actor', 'user_id', 'password', 'must_change'},
    'update_user': {'actor', 'user_id', 'name', 'email'},
    'set_roles': {'actor', 'user_id', 'role_codes'},
}


class AdminService(models.AbstractModel):
    _name = 'ncollection.admin.service'
    _description = 'Workspace user administration for the platform'

    # ------------------------------------------------------------------ guards
    @api.model
    def _service_only(self, method, vals):
        user = self.env.user
        if not user.has_group('ncollection_core.group_config_sync') or user.share or user.nc_platform_proxy:
            raise AccessError(self.env._("Only the platform service channel can use this."))
        if not isinstance(vals, dict) or set(vals) - _KEYS[method]:
            raise UserError(self.env._("Malformed request."))
        actor = vals.get('actor')
        if (not isinstance(actor, dict) or set(actor) - {'ref', 'name'}
                or not _REF_RE.fullmatch(str(actor.get('ref') or ''))):
            raise UserError(self.env._("The request does not say which platform administrator made it."))
        name = str(actor.get('name') or '').strip()[:80]
        # The superuser for the ONE operation (the res.users guards recognise exactly this); the actor is carried for
        # the audit row and never used to decide anything
        return self.with_user(SUPERUSER_ID).with_context(
            nc_platform_actor='platform:%s (%s)' % (actor['ref'], name)).env

    @api.model
    def _target(self, env, user_id):
        if isinstance(user_id, bool) or not isinstance(user_id, int):
            raise UserError(self.env._("Malformed request."))
        user = env['res.users'].with_context(active_test=False).browse(user_id).exists()
        if (not user or user.share or user.nc_platform_proxy or user.login.endswith(_SERVICE_LOGIN_SUFFIX)
                or user.login == '__system__' or user.id == 1
                or user.has_group('ncollection_core.group_config_sync')
                or user.has_group('ncollection_core.group_cron_service')):
            raise UserError(self.env._("That account is not managed here."))
        return user

    @api.model
    def _roles(self, env, codes):
        codes = [str(code) for code in (codes or [])]
        if any(not _CODE_RE.fullmatch(code) for code in codes):
            raise UserError(self.env._("Malformed request."))
        roles = env['ncollection.role'].search([('code', 'in', codes)])
        if set(roles.mapped('code')) != set(codes):
            raise UserError(self.env._("Unknown role."))
        return roles

    # ------------------------------------------------------------------ reads
    @api.model
    def list_users(self, vals):
        env = self._service_only('list_users', vals)
        owner = env.ref('ncollection_core.group_role_owner')
        users = env['res.users'].with_context(active_test=False).search(
            [('share', '=', False), ('nc_platform_proxy', '=', False)], order='active desc, name')
        rows = []
        for user in users:
            if user.login.endswith(_SERVICE_LOGIN_SUFFIX) or user.login == '__system__' \
                    or user.has_group('ncollection_core.group_config_sync') \
                    or user.has_group('ncollection_core.group_cron_service'):
                continue
            rows.append({
                'id': user.id, 'name': user.name, 'login': user.login, 'email': user.email or '',
                'active': user.active, 'administrator': owner in user.all_group_ids,
                'roles': user.nc_role_ids.mapped('name'), 'role_codes': user.nc_role_ids.mapped('code'),
                'must_change_password': user.nc_must_change_password,
                'last_login': user.login_date and user.login_date.isoformat() or '',
            })
        config = env['ncollection.workspace.config']
        return {'users': rows, 'max_users': config.get_max_users(),
                'active_internal': sum(1 for row in rows if row['active'])}

    @api.model
    def list_roles(self, vals):
        env = self._service_only('list_roles', vals)
        roles = env['ncollection.role'].search([], limit=200)
        return {'roles': [{
            'code': role.code, 'name': role.name, 'kind': role.kind, 'available': role.available,
            'needs': role.missing_apps or '', 'administrator': role.grants_administration,
            'description': role.description or ''} for role in roles]}

    # ------------------------------------------------------------------ operations
    @api.model
    def create_user(self, vals):
        env = self._service_only('create_user', vals)
        roles = self._roles(env, vals.get('role_codes'))
        user, generated = env['res.users']._nc_create_workspace_user(
            vals.get('name'), vals.get('login'), email=vals.get('email'), role_ids=roles.ids,
            password=vals.get('password') or None, must_change=vals.get('must_change', True))
        return {'ok': True, 'user_id': user.id, 'login': user.login, 'temporary_password': generated}

    @api.model
    def set_active(self, vals):
        env = self._service_only('set_active', vals)
        user = self._target(env, vals.get('user_id'))
        user.nc_set_active(bool(vals.get('active')))
        return {'ok': True, 'active': user.active}

    @api.model
    def reset_password(self, vals):
        env = self._service_only('reset_password', vals)
        user = self._target(env, vals.get('user_id'))
        temporary = user.nc_reset_password(vals.get('password') or None, must_change=vals.get('must_change', True))
        return {'ok': True, 'temporary_password': temporary}

    @api.model
    def update_user(self, vals):
        env = self._service_only('update_user', vals)
        user = self._target(env, vals.get('user_id'))
        user.nc_update_profile(name=vals.get('name'), email=vals.get('email'))
        return {'ok': True, 'name': user.name, 'email': user.email or ''}

    @api.model
    def set_roles(self, vals):
        env = self._service_only('set_roles', vals)
        user = self._target(env, vals.get('user_id'))
        user.nc_set_roles(self._roles(env, vals.get('role_codes')).ids)
        return {'ok': True, 'roles': user.nc_role_ids.mapped('name')}
