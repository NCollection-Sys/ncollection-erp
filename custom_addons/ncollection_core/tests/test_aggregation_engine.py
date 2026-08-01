# -*- coding: utf-8 -*-
"""Aggregation engine — correctness of the uncached path (P4-T01).

The properties worth protecting are the ones that fail silently or dangerously:

1. **Soft dependencies.** A spec naming a model this tenant never installed must
   be DROPPED, not raised. That is what lets a CRM-only tenant use a dashboard
   built against sale/account/stock/hr.
2. **Ring 2 is inherited, not re-implemented.** A model the plan does not
   license for this user must be dropped too — via the AccessError P1-T10
   raises, never via the engine reading the plan itself.
3. **Fail-open.** No malformed spec, no denied read, no ORM error may escape
   into a caller. A missing tile is a UX bug; a 500 is an outage.
4. **Cached payloads stay inert.** ``_read_group`` hands back recordsets for
   many2one groupby values. Those must be flattened before they can be cached,
   or the cache would hold live ORM objects bound to a dead environment.
"""

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestAggregationEngine(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Engine = cls.env["ncollection.aggregation.engine"]

    # -- spec validation ----------------------------------------------------

    def test_malformed_specs_are_dropped_not_raised(self):
        """Every malformed shape returns None instead of raising (rule 3)."""
        malformed = [
            None,
            "not-a-dict",
            {},                                            # no key
            {"key": "k"},                                  # no model
            {"key": "k", "model": "res.partner"},          # neither agg nor groupby
            {"key": "k", "model": "res.partner", "aggregates": "notalist"},
            {"key": "k", "model": "res.partner", "aggregates": ["id:nonsense"]},
            {"key": "k", "model": "res.partner", "groupby": "notalist"},
            {"key": "k", "model": "res.partner", "aggregates": ["__count"],
             "domain": "notalist"},
        ]
        for spec in malformed:
            with self.subTest(spec=spec):
                self.assertIsNone(
                    self.Engine.aggregate(spec),
                    "malformed spec should be dropped, not raised: %r" % (spec,),
                )

    def test_valid_spec_passes_validation(self):
        self.assertIsNone(self.Engine._validate_spec({
            "key": "partners", "model": "res.partner",
            "domain": [], "aggregates": ["__count"],
        }))

    def test_bad_order_term_is_rejected_at_validation(self):
        """A malformed `order` must fail LOUDLY at validation.

        Regression: `_read_group` raises ValueError for an order term missing
        its aggregate suffix ('amount desc' instead of 'amount:sum desc'), and
        the fail-open handler swallowed it — the caller got an empty tile and
        the benchmark reported a passing budget for a query that never ran.
        Validation now names the mistake instead.
        """
        error = self.Engine._validate_spec({
            "key": "top", "model": "res.partner",
            "groupby": ["parent_id"], "aggregates": ["id:count"],
            "order": "id desc",
        })
        self.assertIsNotNone(error, "bad order term must be rejected")
        self.assertIn("orders by", error)

    def test_valid_order_term_passes_validation(self):
        for order in ("id:count desc", "parent_id", "id:count desc, parent_id"):
            with self.subTest(order=order):
                self.assertIsNone(self.Engine._validate_spec({
                    "key": "top", "model": "res.partner",
                    "groupby": ["parent_id"], "aggregates": ["id:count"],
                    "order": order,
                }))

    # -- soft dependencies --------------------------------------------------

    def test_absent_model_is_dropped(self):
        """A model no tenant in Community has must drop, not raise (rule 1)."""
        self.assertFalse(self.Engine._model_available("no.such.model.p4t01"))
        self.assertIsNone(self.Engine.aggregate({
            "key": "ghost", "model": "no.such.model.p4t01",
            "aggregates": ["__count"],
        }))

    def test_absent_model_is_omitted_from_batch(self):
        """aggregate_many omits dropped specs entirely — never a None value.

        A consumer must not have to tell "present but empty" from "absent"; that
        distinction is exactly how license state leaks into a UI.
        """
        results = self.Engine.aggregate_many([
            {"key": "real", "model": "res.partner", "aggregates": ["__count"]},
            {"key": "ghost", "model": "no.such.model.p4t01",
             "aggregates": ["__count"]},
        ])
        self.assertIn("real", results)
        self.assertNotIn("ghost", results)

    # -- Ring 2 inheritance -------------------------------------------------

    def test_unreadable_model_is_dropped(self):
        """A model raising AccessError on read is dropped (rule 2).

        Simulated by patching the probe rather than by building a whole
        unlicensed tenant: the contract under test is "AccessError means drop",
        and P1-T10 owns the question of when AccessError is raised.
        """
        engine = self.Engine

        def _deny(model_name):
            raise AccessError("denied by plan")

        self.patch(type(engine), "_model_available", lambda self, m: True)
        self.patch(type(engine), "_read_group_uncached",
                   lambda self, *a, **kw: _deny(a[0]))
        # _model_readable does its own probe; make that deny too.
        self.patch(type(engine), "_model_readable", lambda self, m: False)
        self.assertIsNone(engine.aggregate({
            "key": "denied", "model": "res.partner",
            "aggregates": ["__count"],
        }))

    # -- aggregation correctness -------------------------------------------

    def test_count_matches_search_count(self):
        """The engine's number must equal the ORM's own count."""
        domain = [("is_company", "=", True)]
        expected = self.env["res.partner"].search_count(domain)
        result = self.Engine.aggregate({
            "key": "companies", "model": "res.partner",
            "domain": domain, "aggregates": ["__count"],
        })
        self.assertIsNotNone(result)
        self.assertEqual(result["key"], "companies")
        self.assertEqual(result["rows"][0][0], expected)

    def test_groupby_rows_are_inert_data(self):
        """many2one groupby cells must be flattened to (id, label) (rule 4).

        A recordset in a cached payload would be bound to an environment that is
        gone by the time the next request reads it.
        """
        self.env["res.partner"].create({
            "name": "P4T01 probe child",
            "parent_id": self.env["res.partner"].create(
                {"name": "P4T01 probe parent", "is_company": True}).id,
        })
        result = self.Engine.aggregate({
            "key": "by_parent", "model": "res.partner",
            "domain": [("parent_id", "!=", False)],
            "groupby": ["parent_id"], "aggregates": ["__count"],
        })
        self.assertIsNotNone(result)
        self.assertTrue(result["rows"], "expected at least one group")
        for row in result["rows"]:
            for cell in row:
                self.assertNotIsInstance(
                    cell, self.env["res.partner"].__class__,
                    "recordset leaked into a cacheable payload",
                )
        first_cell = result["rows"][0][0]
        self.assertIsInstance(first_cell, tuple)
        self.assertEqual(len(first_cell), 2, "expected (id, display_name)")
        self.assertIsInstance(first_cell[0], int)
        self.assertIsInstance(first_cell[1], str)
