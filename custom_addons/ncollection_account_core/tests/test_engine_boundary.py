# -*- coding: utf-8 -*-
"""F1-T02: the accounting engine-boundary guard.

"Odoo owns accounting" (FINANCIAL_PLATFORM_ARCHITECTURE §4/§6). This suite FAILS
if any ``ncollection_*`` module overrides a core posting / tax / reconcile
COMPUTATION method on the Odoo accounting engine — i.e. reimplements the engine.
Extending it with new fields or NEW methods is fine (that is how the financial
platform is built); only these exact engine-computation entry points are
guarded. Workflow hooks (``action_post``, ``button_draft`` …) are deliberately
NOT guarded — NCollection legitimately owns approval chains / notifications that
wrap them via ``super()``.
"""
from odoo.tests import TransactionCase, tagged

# Core engine-computation methods NCollection must never override. All verified
# to exist in Odoo 19 Community `account`. Guarding a method the engine renames
# later simply becomes inert (no override is ever found) — never a false pass
# for a DIFFERENT override, since each is matched per (model, method).
_FORBIDDEN = {
    'account.move': {'_post', '_compute_tax_totals', '_reverse_moves'},
    'account.move.line': {'_compute_balance', '_compute_amount_currency'},
    'account.tax': {'compute_all'},
    'account.payment': {'_synchronize_to_moves'},
}
_NC_PREFIX = 'odoo.addons.ncollection'


@tagged('post_install', '-at_install')
class TestEngineBoundary(TransactionCase):

    def test_no_ncollection_module_overrides_the_accounting_engine(self):
        """No ncollection class in an engine model's MRO may define one of the
        guarded posting/tax/reconcile methods."""
        violations = []
        for model_name, methods in _FORBIDDEN.items():
            model = self.env.get(model_name)
            if model is None:
                continue
            for klass in type(model).mro():
                origin = getattr(klass, '__module__', '') or ''
                if not origin.startswith(_NC_PREFIX):
                    continue
                for method in sorted(methods & set(vars(klass))):
                    violations.append(
                        "%s overrides engine method %s.%s()"
                        % (origin, model_name, method))
        self.assertEqual(violations, [], self._msg(violations))

    def test_guarded_methods_still_exist_on_the_engine(self):
        """Guard against silent rot: if Odoo renames a guarded method, the guard
        for it goes inert — surface that so the list is kept current."""
        stale = []
        for model_name, methods in _FORBIDDEN.items():
            model = self.env.get(model_name)
            if model is None:
                continue
            for method in sorted(methods):
                if not hasattr(model, method):
                    stale.append("%s.%s()" % (model_name, method))
        self.assertEqual(
            stale, [],
            "These guarded engine methods no longer exist (Odoo renamed them?) "
            "— update _FORBIDDEN so the boundary guard keeps biting: %s" % stale)

    @staticmethod
    def _msg(violations):
        return (
            "Engine-boundary violation (FPA §4/§6 — Odoo owns accounting):\n  "
            + "\n  ".join(violations)
            + "\nNCollection modules must not reimplement core posting / tax / "
              "reconcile computation. Add fields or NEW methods instead, or seek "
              "explicit architectural approval (FPA §6).")
