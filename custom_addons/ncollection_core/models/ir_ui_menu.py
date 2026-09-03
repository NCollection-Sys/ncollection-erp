# -*- coding: utf-8 -*-
"""Ring 1 of license defense-in-depth: menu visibility (P1-T09).

Hides the menu trees of applications not licensed by the tenant's plan
(ncollection.workspace.config.allowed_module_names). UI/UX ONLY — this
provides ZERO security (ARCHITECTURE_SECURITY §4 Ring 1); the ORM/RPC
enforcement layer is P1-T10.

Design:
- A root menu (parent_id = False) is blocked when its xml-id belongs to a
  module that is neither whitelisted nor in the allowed list. Descendants
  are blocked via parent_path prefix — explicit, not relying on client-side
  tree pruning.
- FAIL-OPEN on every uncertain path (no config record, empty allowed list,
  upstream signature drift): wrongly-visible menus are a UX bug; a broken
  webclient is an outage. Ring 2 still enforces the license either way.
- `_visible_menu_ids` is @ormcache'd upstream (and consumed by the also-
  cached load_menus). workspace.config create/write/unlink clears the
  registry cache, which is what makes "menus update after cache clear" true.
"""

import inspect
import logging

from odoo import api, models
from odoo.addons.base.models.ir_ui_menu import IrUiMenu as IrUiMenuBase

_logger = logging.getLogger(__name__)

# Modules whose menus are never filtered, whatever the plan says.
MENU_MODULE_WHITELIST = {'base', 'web', 'mail', 'bus'}
NCOLLECTION_PREFIX = 'ncollection_'

# Modules whose menus mount UNDER another app's menu (no root menu of their own),
# so the root-menu-owner candidate derivation below cannot see them (#240). List
# them explicitly so a downgraded tenant actually loses them via BOTH rings; the
# blocked/allowed filter still applies, so a plan that DOES license them keeps
# them. These are the OCA financial-report modules plan-gated by P3-T01 (retired
# fleet-wide at F2-T07). Add a module here if it nests all its menus under
# another app AND must be plan-gated.
MENU_NESTED_GATED_MODULES = {'account_financial_report', 'mis_builder'}

# Root menus that are NOT customer applications and are therefore left out of
# the tenant app launcher (#455). They are not hidden — a user who may see them
# still reaches them from the sidebar, and Apps/Settings remain owner-only via
# _OWNER_ONLY_MENUS below. This list only decides what belongs on a CUSTOMER's
# home grid: administration surfaces and internal/dev tooling do not.
#
# The launcher's own menu is excluded for the obvious reason — a home screen
# that lists itself as one of its apps is a loop.
LAUNCHER_EXCLUDED_MENUS = frozenset({
    'base.menu_management',                        # Apps
    'base.menu_administration',                    # Settings
    'base.menu_tests',                             # Odoo's own test menu
    'ncollection_branding.menu_component_playground',  # internal design tooling
    'ncollection_core.menu_ncollection_home_root',  # the launcher itself
})

# P1-T09 risk note: _visible_menu_ids is an Odoo-internal method. Pin the
# exact Odoo 19 signature and refuse to filter if upstream drifts.
_EXPECTED_PARAMS = ('self', 'debug')
try:
    _actual = tuple(
        inspect.signature(IrUiMenuBase._visible_menu_ids).parameters
    )
    SIGNATURE_OK = _actual == _EXPECTED_PARAMS
except Exception:  # pragma: no cover - defensive: never break registry load
    SIGNATURE_OK = False

if not SIGNATURE_OK:  # pragma: no cover
    _logger.critical(
        "ir.ui.menu._visible_menu_ids signature changed upstream "
        "(expected %s, got %s) — menu license filtering is DISABLED "
        "(fail-open). Re-pin ncollection_core/models/ir_ui_menu.py.",
        _EXPECTED_PARAMS, _actual if '_actual' in dir() else '?',
    )


class IrUiMenu(models.Model):
    _inherit = 'ir.ui.menu'

    # Owner-only top menus (P1-T11). Hidden dynamically here rather than by
    # static group_ids on the menu records, because installing other modules
    # reprocesses these core menuitems and wipes static group changes (the
    # Apps menu in particular). Request-time subtraction is immune to that.
    _OWNER_ONLY_MENUS = ('base.menu_management', 'base.menu_administration')

    @api.model
    def _visible_menu_ids(self, debug=False):
        visible = super()._visible_menu_ids(debug=debug)
        if not SIGNATURE_OK:
            return visible
        blocked = self._ncollection_blocked_menu_ids()
        if blocked:
            visible = visible - blocked
        owner_only = self._ncollection_owner_only_menu_ids()
        if owner_only:
            visible = visible - owner_only
        return visible

    @api.model
    def _ncollection_owner_only_menu_ids(self):
        """Apps/Settings menu subtrees to hide from non-Owner users.

        Empty for the Owner, the superuser, and when no Owner role exists
        (fail-open). Covers each menu's whole subtree via parent_path.
        """
        user = self.env.user
        if self.env.su or user._is_superuser():
            return set()
        if user.has_group('ncollection_core.group_role_owner'):
            return set()
        roots = self.sudo().browse([
            m.id for xmlid in self._OWNER_ONLY_MENUS
            if (m := self.env.ref(xmlid, raise_if_not_found=False))
        ])
        if not roots:
            return set()
        # Each root + its whole subtree (parent_path prefix match). A
        # targeted =like per root avoids loading every menu in the DB.
        menu_ids = set(roots.ids)
        Menu = self.sudo().with_context(active_test=False)
        for root in roots:
            if root.parent_path:
                menu_ids.update(
                    Menu.search([('parent_path', '=like', root.parent_path + '%')]).ids
                )
        return menu_ids

    # ------------------------------------------------------------------
    # Tenant app launcher (#455)
    # ------------------------------------------------------------------
    @api.model
    def nc_tenant_apps(self):
        """The apps to show a tenant user on their home launcher.

        DERIVED, never declared. The source is ``get_user_roots()`` — Odoo's
        own "root menus this user may see" — which runs through
        ``_filter_visible_menus`` -> ``_visible_menu_ids``, i.e. THIS class's
        override. So every app returned here has already passed:

          * the user's group permissions (core behaviour),
          * Ring 1 plan licensing, ``allowed_module_names`` + its dependency
            closure (``_ncollection_blocked_menu_ids`` above), and
          * the owner-only Apps/Settings subtraction (P1-T11).

        That is the whole point: there is no second list to keep in step, so a
        module cannot appear here that the tenant is not licensed for, and
        nothing needs re-checking when the plan changes — the sync that
        rewrites ``allowed_module_names`` moves this too.

        ``web_icon_data`` is the module's OWN official icon as Odoo already
        stores it; no icon is invented here.
        """
        roots = self.get_user_roots()
        if not roots:
            return []
        xmlids = roots._get_menuitems_xmlids()
        apps = []
        for menu in roots:
            xmlid = xmlids.get(menu.id, '')
            if xmlid in LAUNCHER_EXCLUDED_MENUS:
                continue
            target = menu._nc_actionable_menu()
            if not target:
                # NO REACHABLE ACTION -> NOT AN APP (#459). A card that cannot
                # open anything is worse than an absent one: it advertises a
                # feature and then does nothing when clicked.
                continue
            action = target.action
            apps.append({
                'id': menu.id,
                # The menu that actually OWNS the action. For CRM, Calendar and
                # Contacts this is a CHILD: their root menus are containers with
                # no action of their own, which is exactly why every such card
                # was inert (#459). The client hands this to the menu service so
                # navigation and the current-app highlight match a sidebar click.
                'menu_id': target.id,
                'xmlid': xmlid,
                'name': menu.name,
                # `action` is a reference field ("ir.actions.act_window,42");
                # the client needs the bare id to open it.
                'action_id': action.id,
                'action_model': action._name,
                'web_icon': menu.web_icon or '',
                'web_icon_data': menu.web_icon_data.decode() if menu.web_icon_data else '',
                'sequence': menu.sequence,
            })
        apps.sort(key=lambda a: (a['sequence'], a['name'] or ''))
        return apps

    def _nc_actionable_menu(self):
        """This menu if it carries an action, else its first descendant that
        does — the menu a click should actually open (#459).

        Odoo's app roots are frequently pure containers (`crm.crm_menu_root`,
        `contacts.menu_contacts`, `calendar.mail_menu_calendar` all have
        `action = False`), with the action living on a child. The sidebar
        resolves this by drilling into the tree; the launcher has to do the
        same or its cards do nothing.

        Ordered by (sequence, id) so the target is the same menu the sidebar
        would land on, and searched through ``self`` — NOT sudo — so a
        descendant the user may not see is never chosen as their entry point.
        """
        self.ensure_one()
        if self.action:
            return self
        if not self.parent_path:
            return self.browse()
        descendants = self.search(
            [('parent_path', '=like', self.parent_path + '%'),
             ('id', '!=', self.id),
             ('action', '!=', False)],
            order='sequence, id')
        return descendants[:1]

    # ------------------------------------------------------------------
    # Blocking computation (split for testability)
    # ------------------------------------------------------------------
    @api.model
    def _ncollection_blocked_module_names(self):
        """Module names whose menu trees must be hidden.

        Fail-open: empty set on no config / no allowed list / any error.
        """
        try:
            config = self.env['ncollection.workspace.config'].sudo().get_config()
            if not config:
                return set()
            allowed = set(config.get_allowed_module_list())
            if not allowed:
                return set()
            # A plan lists FEATURE modules; a feature that pulls an OCA module in
            # as a DEPENDENCY licenses that dependency too — e.g. the Enterprise
            # plan lists `ncollection_mis_templates`, which depends on
            # `mis_builder`, so `mis_builder` is licensed even though it is not
            # named literally. Expand `allowed` to its dependency closure so a
            # dependency is never gated out from under the feature that needs it.
            allowed = self._ncollection_expand_dependencies(allowed)

            # Candidate modules = owners of root menus' xml-ids PLUS the known
            # menu-nested modules that own no root menu of their own (#240) — the
            # filter below still exempts the allowed closure / whitelist / ncollection_*.
            root_menus = self.sudo().with_context(active_test=False).search(
                [('parent_id', '=', False)]
            )
            data = self.env['ir.model.data'].sudo().search_read(
                [('model', '=', 'ir.ui.menu'), ('res_id', 'in', root_menus.ids)],
                ['module'],
            )
            candidates = {d['module'] for d in data} | MENU_NESTED_GATED_MODULES
            return {
                name for name in candidates
                if name not in MENU_MODULE_WHITELIST
                and not name.startswith(NCOLLECTION_PREFIX)
                and name not in allowed
            }
        except Exception:  # pragma: no cover - defensive fail-open
            _logger.critical(
                "License blocked-module derivation failed — menu/ORM gating "
                "DISABLED (fail-open). ncollection_core/models/ir_ui_menu.py.",
                exc_info=True)
            return set()

    @api.model
    def _ncollection_expand_dependencies(self, module_names):
        """Transitive dependency closure of ``module_names`` via
        ir.module.module — licensing a wrapper module licenses everything it
        depends on (so an OCA dependency is not gated out from under it)."""
        Module = self.env['ir.module.module'].sudo()
        result = set(module_names)
        frontier = set(module_names)
        while frontier:
            deps = set(Module.search(
                [('name', 'in', list(frontier))]).mapped('dependencies_id.name'))
            frontier = deps - result
            result |= frontier
        return result

    @api.model
    def _ncollection_blocked_menu_ids(self, blocked_modules=None):
        """Ids of every menu inside a blocked module's root tree.

        ``blocked_modules`` is injectable for tests; defaults to the
        computed set. Menus without an xml-id (user-created) are only
        hidden if they live UNDER a blocked root (parent_path prefix),
        never as roots themselves.
        """
        if blocked_modules is None:
            blocked_modules = self._ncollection_blocked_module_names()
        if not blocked_modules:
            return set()

        # Menus OWNED by a blocked module — root OR nested (#240). A root-menu
        # module hides its whole app tree; a menu-nested module (whose menus live
        # under another app's menu, e.g. account_financial_report under account)
        # hides only its own items + their descendants, leaving the host app menu
        # visible. This is why the search is NOT restricted to parent_id=False.
        data = self.env['ir.model.data'].sudo().search_read(
            [('model', '=', 'ir.ui.menu'), ('module', 'in', list(blocked_modules))],
            ['res_id'],
        )
        owned = self.sudo().with_context(active_test=False).browse(
            [d['res_id'] for d in data]).exists()
        if not owned:
            return set()

        prefixes = tuple(m.parent_path for m in owned if m.parent_path)
        if not prefixes:
            return set(owned.ids)
        all_menus = self.sudo().with_context(active_test=False).search_read(
            [], ['parent_path'],
        )
        return {
            m['id'] for m in all_menus
            if m['parent_path'] and m['parent_path'].startswith(prefixes)
        }
