# -*- coding: utf-8 -*-
"""Workspace roles: named bundles of REAL Odoo groups.

There is no permission engine of our own here. A role is only a convenient, translated name for a set of
``res.groups``; what a user may do is, and stays, decided by those groups, their ACL rows (``ir.model.access``) and
the record rules. Holding a role means holding all of its groups, so the role list, the access matrix and the user
form can never disagree with what Odoo enforces: the "role" is derived from the groups, not stored next to them.

  * STANDARD roles (data) name groups by external id. The ids are resolved when they are needed, so a role whose
    application is not installed in this workspace (no Inventory, no HR...) reports itself as unavailable instead of
    pointing at a group that does not exist, and a workspace picks it up on its own when the application is installed.
  * CUSTOM roles are made by a workspace administrator from the groups of the installed applications. A custom role
    can never carry a group that would hand over administration of the workspace, of the platform or of the service
    accounts (``FORBIDDEN_GROUPS`` and everything that implies them): that is checked on every create / write,
    whichever way the call arrives.
"""
from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

# Groups a custom role may never contain, nor any group that implies one of them (checked on the transitive closure).
FORBIDDEN_GROUPS = (
    'base.group_system',                        # the whole workspace
    'base.group_erp_manager',                   # access rights: could grant itself anything
    'base.group_portal', 'base.group_public',   # not employees
    'ncollection_core.group_config_sync',       # platform service accounts
    'ncollection_core.group_cron_service',
    'ncollection_core.group_role_owner',        # the standard Administrator role, never re-created by hand
)
ADMIN_GROUP = 'base.group_system'
_STANDARD_EDITABLE = {'active', 'sequence', 'description', 'name'}


class NcollectionRole(models.Model):
    _name = 'ncollection.role'
    _description = 'Workspace role'
    _order = 'sequence, name, id'

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, index=True, copy=False, readonly=True,
                       help="Stable identifier of the role. Standard roles keep theirs across upgrades.")
    description = fields.Text(translate=True)
    kind = fields.Selection([('standard', 'Standard'), ('custom', 'Custom')], required=True, default='custom',
                            readonly=True)
    sequence = fields.Integer(default=50)
    active = fields.Boolean(default=True)
    group_xmlids = fields.Char(
        readonly=True, copy=False,
        help="Standard roles: the external ids of the access groups this role stands for, comma separated.")
    custom_group_ids = fields.Many2many(
        'res.groups', 'ncollection_role_group_rel', 'role_id', 'group_id', string='Groups',
        help="Custom roles: the access groups this role grants.")

    group_ids = fields.Many2many('res.groups', string='Groups granted', compute='_compute_group_ids')
    available = fields.Boolean(compute='_compute_group_ids', string='Available in this workspace')
    missing_apps = fields.Char(compute='_compute_group_ids', string='Needs')
    grants_administration = fields.Boolean(compute='_compute_group_ids', string='Administrator level')
    user_count = fields.Integer(compute='_compute_user_count', string='Users')

    _code_unique = models.Constraint('unique(code)', 'A role code can only be used once.')

    # ------------------------------------------------------------------ the groups behind a role
    def _nc_xmlid_groups(self):
        """(groups that exist, external ids that do not) for the standard role's ``group_xmlids``."""
        self.ensure_one()
        found = self.env['res.groups']
        missing = []
        for xmlid in (self.group_xmlids or '').split(','):
            xmlid = xmlid.strip()
            if not xmlid:
                continue
            group = self.env.ref(xmlid, raise_if_not_found=False)
            if group and group._name == 'res.groups':
                found |= group
            else:
                missing.append(xmlid)
        return found, missing

    @api.depends('kind', 'group_xmlids', 'custom_group_ids')
    def _compute_group_ids(self):
        admin = self.env.ref(ADMIN_GROUP)
        for role in self:
            if role.kind == 'standard':
                groups, missing = role._nc_xmlid_groups()
            else:
                groups, missing = role.custom_group_ids, []
            role.group_ids = groups
            role.available = bool(groups) and not missing
            role.missing_apps = ', '.join(sorted({xmlid.split('.')[0] for xmlid in missing}))
            role.grants_administration = admin in groups.all_implied_ids

    def _compute_user_count(self):
        users = self.env['res.users'].sudo().search([('share', '=', False), ('nc_platform_proxy', '=', False)])
        # the role a person is known by, not every role they cover
        primary = {user.id: user.nc_role_ids for user in users}
        for role in self:
            role.user_count = sum(1 for roles in primary.values() if role in roles)

    # ------------------------------------------------------------------ rules
    @api.model
    def _nc_forbidden_groups(self):
        groups = self.env['res.groups']
        for xmlid in FORBIDDEN_GROUPS:
            group = self.env.ref(xmlid, raise_if_not_found=False)
            if group:
                groups |= group
        return groups

    @api.constrains('kind', 'custom_group_ids')
    def _check_custom_groups(self):
        forbidden = self._nc_forbidden_groups()
        for role in self.filtered(lambda r: r.kind == 'custom'):
            if not role.custom_group_ids:
                raise ValidationError(self.env._("The role %s needs at least one group.", role.name))
            reaches = role.custom_group_ids.all_implied_ids & forbidden
            if reaches:
                raise ValidationError(self.env._(
                    "The role %(role)s cannot grant %(groups)s: it would give administration of the workspace.",
                    role=role.name, groups=', '.join(reaches.mapped('display_name'))))

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            for vals in vals_list:
                self._nc_require_administrator()
                if vals.get('kind') == 'standard' or vals.get('group_xmlids') or vals.get('code'):
                    raise AccessError(self.env._("Standard roles are provided by the product."))
                vals['kind'] = 'custom'
                vals['code'] = self._nc_new_code(vals.get('name'))
        roles = super().create(vals_list)
        roles._nc_audit('role_define', 'created')
        return roles

    def write(self, vals):
        if not self.env.su:
            self._nc_require_administrator()
            if self.filtered(lambda r: r.kind == 'standard') and set(vals) - _STANDARD_EDITABLE:
                raise AccessError(self.env._(
                    "A standard role is defined by the product: you can rename or archive it, not change its groups."))
            if {'kind', 'code', 'group_xmlids'} & set(vals):
                raise AccessError(self.env._("The type and code of a role cannot be changed."))
        result = super().write(vals)
        if 'custom_group_ids' in vals or 'active' in vals:
            self._nc_audit('role_define', 'changed')
        return result

    @api.ondelete(at_uninstall=False)
    def _unlink_except_administrator_or_standard(self):
        if not self.env.su:
            self._nc_require_administrator()
            if self.filtered(lambda r: r.kind == 'standard'):
                raise AccessError(self.env._("A standard role cannot be deleted: archive it instead."))

    def unlink(self):
        self._nc_audit('role_define', 'deleted')
        return super().unlink()

    @api.model
    def _nc_require_administrator(self):
        if not self.env.user.has_group(ADMIN_GROUP):
            raise AccessError(self.env._("Only a workspace administrator can manage roles."))

    @api.model
    def _nc_new_code(self, name):
        base = 'custom_' + ''.join(ch if ch.isalnum() else '_' for ch in (name or 'role').lower())[:40]
        code, n = base, 1
        while self.sudo().with_context(active_test=False).search_count([('code', '=', code)]):
            n += 1
            code = '%s_%d' % (base, n)
        return code

    def _nc_audit(self, event, what):
        log = self.env.get('ncollection.auth.log')
        if log is None:
            return
        for role in self:
            log._capture_admin(event, actor=self.env.user, detail='%s %s (%s)' % (role.kind, what, role.code))

    def action_view_access_matrix(self):
        """Open the role's access matrix: lines rebuilt from Odoo's access rows (see wizards/role_access_line.py)."""
        self.ensure_one()
        Line = self.env['ncollection.role.access.line']
        Line.search([('role_id', '=', self.id)]).unlink()
        Line.create([{
            'role_id': self.id, 'model_name': row['model'], 'model_label': row['name'],
            'can_read': row['read'], 'can_write': row['write'],
            'can_create': row['create'], 'can_unlink': row['unlink'],
        } for row in self._nc_access_matrix()])
        return {
            'type': 'ir.actions.act_window', 'name': self.env._('Access: %s', self.name),
            'res_model': 'ncollection.role.access.line', 'view_mode': 'list', 'target': 'current',
            'domain': [('role_id', '=', self.id)],
            'views': [(self.env.ref('ncollection_core.view_role_access_line_list').id, 'list')],
            'search_view_id': self.env.ref('ncollection_core.view_role_access_line_search').id,
        }

    def action_view_users(self):
        self.ensure_one()
        users = self.env['res.users'].sudo().search([('share', '=', False), ('nc_platform_proxy', '=', False)])
        holders = users.filtered(lambda user: self in user.nc_role_ids)
        return {
            'type': 'ir.actions.act_window', 'name': self.env._('Users with %s', self.name), 'res_model': 'res.users',
            'view_mode': 'list,form', 'domain': [('id', 'in', holders.ids)],
            'views': [(self.env.ref('ncollection_core.view_workspace_users_list').id, 'list'),
                      (self.env.ref('ncollection_core.view_workspace_users_form').id, 'form')],
        }

    # ------------------------------------------------------------------ the access matrix: read from Odoo, not kept
    def _nc_access_matrix(self):
        """What this role's groups really allow: one row per model that an ACL row of any of them (or of a group they
        imply) opens, with the union of the four rights. Read straight from ``ir.model.access``; nothing is cached or
        copied, so it is exactly what Odoo enforces."""
        self.ensure_one()
        groups = self.group_ids.all_implied_ids
        if not groups:
            return []
        rows = self.env['ir.model.access'].sudo().search([('group_id', 'in', groups.ids), ('active', '=', True)])
        # wizards are not data a person works with
        rows = rows.filtered(lambda access: not access.model_id.transient)
        merged = {}
        for access in rows:
            entry = merged.setdefault(access.model_id.id, {
                'model': access.model_id.model, 'name': access.model_id.name,
                'read': False, 'write': False, 'create': False, 'unlink': False})
            for right in ('read', 'write', 'create', 'unlink'):
                entry[right] = entry[right] or bool(access['perm_' + right])
        return sorted(merged.values(), key=lambda entry: entry['model'])
