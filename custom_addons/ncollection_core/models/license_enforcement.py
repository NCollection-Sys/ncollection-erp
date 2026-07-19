# -*- coding: utf-8 -*-
"""Ring 2 of license defense-in-depth: ORM/RPC enforcement (P1-T10).

Menu hiding (Ring 1, ir_ui_menu.py) is UX only. This is the real control:
it denies read/write/create/unlink on models belonging to modules the
tenant's plan does not license, for all non-system users, at the ORM layer
— which is exactly where XML-RPC / JSON-RPC calls land too
(ARCHITECTURE_SECURITY §4 Ring 2).

Enforcement point: ``BaseModel._check_access`` — the shared hook behind
``check_access`` / ``has_access`` / ``_filtered_access`` (Odoo 19,
verified against odoo/orm/models.py). Overriding it once covers every
access path. It returns ``None`` when allowed, or ``(records, factory)``
where ``factory()`` builds the exception — so a branded upsell error slots
straight in.

FAIL-OPEN on every uncertain path (no config, empty allowed list, upstream
signature drift): the worst regression is "license not enforced" (today's
behaviour), never a bricked tenant. Ring 3 (non-installation) is the final
backstop for anything namespace mapping misses.
"""

import functools
import inspect
import logging

from odoo import api, models, tools
from odoo.exceptions import AccessError
from odoo.models import BaseModel

_logger = logging.getLogger(__name__)

_CRUD_OPS = frozenset({'read', 'write', 'create', 'unlink'})

# Model-name namespaces that are NEVER license-gated. Infrastructure and the
# NCollection platform itself must always work, whatever the plan says.
NEVER_BLOCKED_NAMESPACES = (
    'base', 'ir', 'res', 'mail', 'bus', 'web', 'ncollection',
)

# P1-T10 risk note: _check_access is an Odoo-internal method. Pin the Odoo 19
# signature and refuse to enforce if upstream drifts (fail-open).
_EXPECTED_PARAMS = ('self', 'operation')
try:
    _actual = tuple(inspect.signature(BaseModel._check_access).parameters)
    SIGNATURE_OK = _actual == _EXPECTED_PARAMS
except Exception:  # pragma: no cover - never break registry load
    SIGNATURE_OK = False

if not SIGNATURE_OK:  # pragma: no cover
    _logger.critical(
        "BaseModel._check_access signature changed upstream (expected %s, "
        "got %s) — ORM license enforcement is DISABLED (fail-open). Re-pin "
        "ncollection_core/models/license_enforcement.py.",
        _EXPECTED_PARAMS, _actual if '_actual' in dir() else '?',
    )


def _make_license_error(model_name):
    """Return a branded upsell AccessError (rendered by the web client; a
    proper access fault over raw RPC)."""
    return AccessError(
        "The '%s' feature is not included in your NCollection plan. "
        "Upgrade your subscription to unlock it." % model_name
    )


class LicenseEnforcementMixin(models.AbstractModel):
    _inherit = 'base'

    @api.model
    def _ncollection_blocked_namespaces(self):
        """Frozenset of model-name namespace prefixes to deny.

        Cached on the registry; the cache is cleared whenever
        ncollection.workspace.config changes (create/write/unlink call
        env.registry.clear_cache(), P1-T09), so plan changes take effect
        without a restart. Fail-open: empty set when no config / no list.
        """
        return self.env['ir.ui.menu']._ncollection_blocked_namespaces_cached()

    def _check_access(self, operation):
        result = super()._check_access(operation)
        if result is not None:
            return result  # already denied by core — nothing to add
        if not SIGNATURE_OK or operation not in _CRUD_OPS:
            return None
        if self.env.su or self.env.user._is_system():
            return None
        blocked = self._ncollection_blocked_namespaces()
        if not blocked:
            return None
        namespace = self._name.split('.', 1)[0]
        if namespace in blocked:
            return self, functools.partial(_make_license_error, self._name)
        return None


class IrUiMenuLicenseCache(models.Model):
    """Namespace-blocking cache lives on ir.ui.menu so it shares the exact
    invalidation the menu engine already relies on (registry cache clear on
    config change). Kept here, next to Ring 2, for cohesion."""
    _inherit = 'ir.ui.menu'

    @api.model
    @tools.ormcache()
    def _ncollection_blocked_namespaces_cached(self):
        blocked_modules = self.sudo()._ncollection_blocked_module_names()
        namespaces = set()
        for name in blocked_modules:
            ns = name.split('.', 1)[0]
            if ns not in NEVER_BLOCKED_NAMESPACES and not ns.startswith('ncollection'):
                namespaces.add(ns)
        return frozenset(namespaces)
