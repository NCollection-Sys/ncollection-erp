# -*- coding: utf-8 -*-
"""Who may GIVE the System Branding Manager right (the ORM half of that boundary).

``ncollection.product.branding`` refuses anybody who does not hold ``group_system_branding_manager``. That is only a
boundary if holding the group cannot be self-served. A tenant Owner implies ``base.group_system`` and therefore
``base.group_erp_manager``, which Odoo lets write ``res.users.group_ids`` and every relation of ``res.groups``: without
this file an Owner could add the group to their own account, put it in a group they already hold, or make a group they
hold imply it, and then edit the system's identity (measured in the pre-commit review of this change).

The rule here: a change that makes somebody NEW hold the group (directly, or through any implication) is refused unless

* it runs as the superuser itself (``uid == SUPERUSER_ID``: install, upgrade, migrations, provisioning seed, the
  shell). ``sudo()`` is NOT enough: it keeps the acting user, and the workspace's own code runs ``sudo()`` on a
  tenant user's behalf (role assignment does), so an exemption on ``env.su`` would hand the right straight back;
* the acting user is platform staff (``_nc_actor_may_grant_system_branding``, extended where the platform layer
  exists: ``ncollection_subscription``);
* every new holder is an account the platform itself manages (``_nc_system_branding_exempt_holders``, extended by
  ``ncollection_core`` for the Platform Administrator's entry proxy, which is how the entry flow grants it).

Holders are compared BEFORE and AFTER the write, read straight from the relation tables (``res_groups_users_rel`` and
the transitive closure of ``res_groups_implied_rel``), so no field name, reified group field, compute inverse or cache
can step around it. Removing the right is never restricted.

KNOWN LIMIT (documented in SYSTEM_BRANDING.md, deliberately not redesigned): ``base.group_system`` can still edit
``ir.model.access`` / record rules directly, or run server-action code, which reaches the branding model without
holding the group. Closing that means narrowing what Settings administration is in Odoo, which is out of scope.
"""
from odoo import SUPERUSER_ID, api, models
from odoo.exceptions import AccessError

MANAGER_GROUP = 'ncollection_branding.group_system_branding_manager'

# res.groups fields whose change can make a user hold a group (directly or through an implication).
_GROUP_RELATION_FIELDS = frozenset({
    'user_ids', 'all_user_ids', 'implied_ids', 'implied_by_ids', 'all_implied_ids', 'all_implied_by_ids'})


class ResUsers(models.Model):
    _inherit = 'res.users'

    # ------------------------------------------------------------------ the guard
    @api.model_create_multi
    def create(self, vals_list):
        before = self._nc_system_branding_holders_before()
        users = super().create(vals_list)
        self._nc_check_system_branding_holders(before)
        return users

    def write(self, vals):
        before = self._nc_system_branding_holders_before() if self._nc_touches_groups(vals) else None
        result = super().write(vals)
        self._nc_check_system_branding_holders(before)
        return result

    @api.model
    def _nc_touches_groups(self, vals):
        """True when ``vals`` can change what groups a user holds: a relation to ``res.groups``, a computed field with
        an inverse (a compute that writes groups, e.g. a role field), or a key that is not a field at all (Odoo's
        legacy reified group keys). Generous on purpose; plain related fields (name, email, ...) do not count."""
        for name in vals:
            field = self._fields.get(name)
            if field is None or (field.relational and field.comodel_name == 'res.groups'):
                return True
            if field.inverse and not field.related:
                return True
        return False

    # ------------------------------------------------------------------ who may grant / who may hold
    @api.model
    def _nc_actor_may_grant_system_branding(self):
        """The superuser itself only. ``ncollection_subscription`` adds platform staff where the platform layer is."""
        return self.env.uid == SUPERUSER_ID

    def _nc_system_branding_exempt_holders(self):
        """Of the users in ``self`` (new holders), those the platform itself manages. None here; extended in core."""
        return self.browse()

    # ------------------------------------------------------------------ holders, read from the relation tables
    @api.model
    def _nc_system_branding_holders_before(self):
        """The current holders, or None when no check is needed (the actor may grant, or the group does not exist)."""
        if self._nc_actor_may_grant_system_branding():
            return None
        group = self.env.ref(MANAGER_GROUP, raise_if_not_found=False)
        if not group:
            return None
        return self._nc_system_branding_holder_ids(group)

    @api.model
    def _nc_system_branding_holder_ids(self, group):
        self.env['res.users'].flush_model(['group_ids'])
        self.env['res.groups'].flush_model(['implied_ids', 'user_ids'])
        self.env.cr.execute("""
            WITH RECURSIVE implying(gid) AS (
                SELECT %s
                UNION
                SELECT rel.gid FROM res_groups_implied_rel rel JOIN implying ON rel.hid = implying.gid
            )
            SELECT DISTINCT uid FROM res_groups_users_rel WHERE gid IN (SELECT gid FROM implying)
        """, [group.id])
        return {row[0] for row in self.env.cr.fetchall()}

    @api.model
    def _nc_check_system_branding_holders(self, before):
        if before is None:
            return
        group = self.env.ref(MANAGER_GROUP)
        gained = self.env['res.users'].sudo().with_context(active_test=False).browse(
            sorted(self._nc_system_branding_holder_ids(group) - before))
        gained -= gained._nc_system_branding_exempt_holders()
        if gained:
            raise AccessError(self.env._(
                "Only platform staff can give the System Branding Manager right: it changes the identity of the "
                "system, not of this workspace."))


class ResGroups(models.Model):
    _inherit = 'res.groups'

    @api.model_create_multi
    def create(self, vals_list):
        Users = self.env['res.users']
        before = Users._nc_system_branding_holders_before()
        groups = super().create(vals_list)
        Users._nc_check_system_branding_holders(before)
        return groups

    def write(self, vals):
        Users = self.env['res.users']
        before = Users._nc_system_branding_holders_before() if _GROUP_RELATION_FIELDS & set(vals) else None
        result = super().write(vals)
        Users._nc_check_system_branding_holders(before)
        return result
