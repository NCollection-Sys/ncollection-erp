# -*- coding: utf-8 -*-
"""The shared financial gate is actually wired to its consumers (#374).

WHY THIS FILE EXISTS. #374 moved a duplicated authorization rule into
`ncollection.financial.gate.mixin` and made two modules inherit it. The
consumers' own suites already prove the gate DENIES the right people — a probe
that neutered `_require_any_group` failed 4 tests plus 1 error across both of
them. So why add this?

Because those tests would also pass if someone later removed the `_inherit`
line and pasted the helpers back in locally. They assert the BEHAVIOUR, not the
SHARING, and the sharing is the entire point of #374: two copies that agree
today are exactly what this ticket removed. A test that cannot tell "one
definition" from "two identical definitions" cannot protect the property.

These tests assert the property directly — the consumers resolve the helpers
THROUGH the mixin, and read the same constant — so re-duplicating fails here
even while every behavioural test stays green.

The mixin is an AbstractModel with no table, so there is nothing to create;
`env[...]` is enough to prove registration and MRO.
"""

from odoo.addons.ncollection_core.models.financial_gate import (
    FINANCIAL_GATE_GROUPS,
)
from odoo.tests import TransactionCase, tagged

_MIXIN = 'ncollection.financial.gate.mixin'

# The models that must consume the mixin rather than carry their own copy.
# ncollection.ai.question is guarded: ncollection_ai is not installed in every
# test database, and a missing model here must read as "not applicable", never
# as a silent pass.
_CONSUMERS = (
    'ncollection.account.dashboard.service',
    'ncollection.ai.question',
)


@tagged('post_install', '-at_install')
class TestFinancialGateMixin(TransactionCase):

    def test_the_mixin_is_registered(self):
        self.assertIn(_MIXIN, self.env,
                      "the shared gate is not in the registry — every consumer "
                      "would raise AttributeError on its first check")

    def test_consumers_inherit_the_helpers_from_the_mixin(self):
        """Resolved THROUGH the mixin, not redefined locally.

        `_inherit` puts the mixin in the MRO, so an unmodified consumer
        resolves the helper to the mixin's own function object. A consumer that
        redefines it locally — the #374 regression — resolves to a different
        one, and this fails.
        """
        mixin_cls = type(self.env[_MIXIN])
        for name in ('_require_any_group', '_require_role_or_technical_admin'):
            expected = getattr(mixin_cls, name)
            for model in _CONSUMERS:
                if model not in self.env:
                    continue        # module not installed in this database
                with self.subTest(model=model, helper=name):
                    self.assertIs(
                        getattr(type(self.env[model]), name), expected,
                        "%s does not resolve %s through %s — it has its own "
                        "copy again, which is the duplication #374 removed"
                        % (model, name, _MIXIN))

    def test_at_least_one_consumer_is_present(self):
        """Stops the loop above from passing vacuously.

        Every `continue` above is a model this database does not have. If they
        were ALL absent the loop would assert nothing and still report success —
        the shape this repo keeps getting caught by.

        SKIP, NOT FAIL, and the difference matters. `make test m=ncollection_core`
        is a documented workflow, and it installs this module ALONE — so no
        consumer exists and there is genuinely nothing to check. The first
        version of this asserted instead, which turned that supported scoped run
        into a failure. A skip is the honest answer: nothing was verified, and
        the skip gate (scripts/ci/check_skips.py) makes it visible rather than
        silent. In CI's full matrix both consumers are installed, so this does
        not skip there — if it ever does, that gate fails and tells us the
        consumers stopped being installed, which is the condition worth knowing.
        """
        present = [m for m in _CONSUMERS if m in self.env]
        if not present:
            self.skipTest(
                "no consumer of %s is installed on this database (scoped run), "
                "so the inheritance assertions checked nothing" % _MIXIN)
        self.assertTrue(present)

    def test_the_gate_tuple_has_the_three_groups_and_the_admin_hatch(self):
        """`base.group_system` is the clause most likely to be 'tidied' away.

        It is not boilerplate: `admin` holds it and holds NO role group — the
        implication runs owner -> system, never the reverse — so dropping it
        locks admin out on every tenant. Measured on #333: removing it produced
        8 failures, only one of which was the test written for it.
        """
        self.assertEqual(
            FINANCIAL_GATE_GROUPS,
            ('ncollection_core.group_role_accountant',
             'ncollection_core.group_role_ceo',
             'base.group_system'))

    def test_every_group_in_the_gate_actually_exists(self):
        """An xmlid typo would silently deny everyone rather than error.

        `has_group` on an unknown xmlid returns False, so a misspelling turns
        the gate into a deny-all that no behavioural test distinguishes from a
        correctly-denied user.
        """
        for xmlid in FINANCIAL_GATE_GROUPS:
            with self.subTest(group=xmlid):
                self.assertTrue(
                    self.env.ref(xmlid, raise_if_not_found=False),
                    "%s does not resolve — has_group would return False for "
                    "everyone and the gate would deny all callers" % xmlid)
