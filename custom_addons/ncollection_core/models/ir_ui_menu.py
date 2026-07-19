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

    @api.model
    def _visible_menu_ids(self, debug=False):
        visible = super()._visible_menu_ids(debug=debug)
        if not SIGNATURE_OK:
            return visible
        blocked = self._ncollection_blocked_menu_ids()
        return visible - blocked if blocked else visible

    # ------------------------------------------------------------------
    # Blocking computation (split for testability)
    # ------------------------------------------------------------------
    @api.model
    def _ncollection_blocked_module_names(self):
        """Module names whose menu trees must be hidden.

        Fail-open: empty set when there is no config or no allowed list.
        """
        config = self.env['ncollection.workspace.config'].sudo().get_config()
        if not config:
            return set()
        allowed = set(config.get_allowed_module_list())
        if not allowed:
            return set()

        # Candidate modules = owners of root menus' xml-ids.
        root_menus = self.sudo().with_context(active_test=False).search(
            [('parent_id', '=', False)]
        )
        data = self.env['ir.model.data'].sudo().search_read(
            [('model', '=', 'ir.ui.menu'), ('res_id', 'in', root_menus.ids)],
            ['module'],
        )
        candidates = {d['module'] for d in data}
        return {
            name for name in candidates
            if name not in MENU_MODULE_WHITELIST
            and not name.startswith(NCOLLECTION_PREFIX)
            and name not in allowed
        }

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

        root_menus = self.sudo().with_context(active_test=False).search(
            [('parent_id', '=', False)]
        )
        data = self.env['ir.model.data'].sudo().search_read(
            [('model', '=', 'ir.ui.menu'), ('res_id', 'in', root_menus.ids),
             ('module', 'in', list(blocked_modules))],
            ['res_id'],
        )
        blocked_roots = self.sudo().browse([d['res_id'] for d in data])
        if not blocked_roots:
            return set()

        prefixes = tuple(root.parent_path for root in blocked_roots if root.parent_path)
        if not prefixes:
            return set(blocked_roots.ids)
        all_menus = self.sudo().with_context(active_test=False).search_read(
            [], ['parent_path'],
        )
        return {
            m['id'] for m in all_menus
            if m['parent_path'] and m['parent_path'].startswith(prefixes)
        }
