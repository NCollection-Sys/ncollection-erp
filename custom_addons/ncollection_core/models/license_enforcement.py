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
        Menu = self.env['ir.ui.menu']
        blocked_ns = Menu._ncollection_blocked_namespaces_cached()
        blocked_models = Menu._ncollection_blocked_models_cached()
        if not blocked_ns and not blocked_models:
            return None
        # Deny by exclusive-namespace (mis.*, trial.*) OR by exact model — the
        # latter catches a blocked module's models that live in a SHARED
        # namespace (account_financial_report's `account.age.report.configuration`
        # under the licensed `account` namespace, #240).
        if self._name.split('.', 1)[0] in blocked_ns or self._name in blocked_models:
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
        return self._ncollection_blocked_access()[0]

    @api.model
    @tools.ormcache()
    def _ncollection_blocked_models_cached(self):
        return self._ncollection_blocked_access()[1]

    @api.model
    def _ncollection_blocked_access(self):
        """``(blocked_namespaces, blocked_models)`` for Ring 2.

        FAIL-OPEN: any error returns empty sets — an unenforced license beats a
        bricked tenant (the module's governing principle). The two ormcache'd
        accessors above call this; on a cache clear it computes twice (cheap,
        amortised — invalidated only on a plan change, not per request)."""
        try:
            return self._ncollection_compute_blocked_access()
        except Exception:  # pragma: no cover - defensive fail-open
            _logger.critical(
                "License blocked-set derivation failed — ORM enforcement DISABLED "
                "(fail-open). ncollection_core/models/license_enforcement.py.",
                exc_info=True)
            return frozenset(), frozenset()

    @api.model
    def _ncollection_compute_blocked_access(self):
        blocked_modules = self.sudo()._ncollection_blocked_module_names()
        if not blocked_modules:
            return frozenset(), frozenset()
        # One scan of every DEFINED model → its namespace + exact name, keyed by
        # owning module(s). ir.model rows are ~thousands; amortised by ormcache.
        rows = self.env['ir.model.data'].sudo().search_read(
            [('model', '=', 'ir.model')], ['module', 'res_id'])
        model_name = {
            m.id: m.model for m in self.env['ir.model'].sudo().browse(
                [r['res_id'] for r in rows]).exists()}
        ns_owners, model_owners = {}, {}
        for r in rows:
            name = model_name.get(r['res_id'])
            if not name:
                continue
            ns_owners.setdefault(name.split('.', 1)[0], set()).add(r['module'])
            model_owners.setdefault(name, set()).add(r['module'])

        def blockable(token):
            return token not in NEVER_BLOCKED_NAMESPACES and not token.startswith('ncollection')

        namespaces = set()
        # (1) module-name prefix — original derivation (crm -> crm.lead; also
        #     drives the synthetic-module tests).
        for name in blocked_modules:
            namespaces.add(name.split('.', 1)[0])
        # (2) namespaces EXCLUSIVELY owned by blocked modules (mis, trial). A
        #     shared namespace (`report.*`, defined by dozens of modules) is NOT
        #     added, so licensed features aren't denied (fail-closed).
        for ns, mods in ns_owners.items():
            if mods <= blocked_modules:
                namespaces.add(ns)
        namespaces = frozenset(ns for ns in namespaces if blockable(ns))
        # (3) exact models EXCLUSIVELY owned by blocked modules whose namespace is
        #     NOT already namespace-blocked — the shared-namespace models, e.g.
        #     account_financial_report's `account.age.report.configuration` under
        #     the licensed `account` namespace (#240). Exact ownership → no
        #     over-block: `account.move` (owned by `account`) is never gated.
        models = frozenset(
            name for name, mods in model_owners.items()
            if mods <= blocked_modules
            and name.split('.', 1)[0] not in namespaces
            and blockable(name.split('.', 1)[0]))
        return namespaces, models
