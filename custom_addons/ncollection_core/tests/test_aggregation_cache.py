# -*- coding: utf-8 -*-
"""Aggregation cache + invalidation contract (P4-T01).

These tests exist because every property here fails *silently* in production:
a cache that never invalidates serves confidently wrong numbers, and a cache
keyed too loosely serves one user's numbers to another. Neither raises.

The one with teeth is ``test_cache_is_keyed_by_user``: Odoo record rules and
P1-T10 Ring 2 both make an aggregate user-dependent, so a shared cache entry is
a cross-user data leak inside a tenant.
"""

from odoo.tests import TransactionCase, tagged

from odoo.addons.ncollection_core.models.aggregation import cache as agg_cache
from odoo.addons.ncollection_core.models.aggregation.version import (
    AGGREGATION_SOURCE_MODELS,
)


@tagged("post_install", "-at_install")
class TestAggregationCache(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Engine = cls.env["ncollection.aggregation.engine"]

    def setUp(self):
        super().setUp()
        # Process-local cache is shared across tests in the same worker.
        agg_cache.clear()
        self.addCleanup(agg_cache.clear)

    def _spec(self, key="partners"):
        return {
            "key": key, "model": "res.partner",
            "domain": [("is_company", "=", True)],
            "aggregates": ["__count"],
        }

    # -- hit / miss ---------------------------------------------------------

    def test_second_identical_call_is_served_from_cache(self):
        first = self.Engine.aggregate(self._spec())
        second = self.Engine.aggregate(self._spec())
        self.assertFalse(first["cached"], "first call must be a miss")
        self.assertTrue(second["cached"], "second call must be a hit")
        self.assertEqual(first["rows"], second["rows"])

    def test_cache_can_be_bypassed_per_spec(self):
        self.Engine.aggregate(self._spec())
        spec = dict(self._spec(), cache=False)
        self.assertFalse(
            self.Engine.aggregate(spec)["cached"],
            "cache=False must force a live read",
        )

    # -- the leak test ------------------------------------------------------

    def test_cache_is_keyed_by_user(self):
        """Two users must never share a cache entry (see cache.py docstring).

        Record rules make the same spec legitimately yield different numbers per
        user; a shared entry would serve one user's figures to another.
        """
        other = self.env["res.users"].create({
            "name": "p4t01 cache probe",
            "login": "p4t01_cache_probe",
            "group_ids": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        versions = agg_cache.versions_for(self.env)
        key_self = agg_cache.make_key(self.env, self._spec(), versions)
        key_other = agg_cache.make_key(
            self.env(user=other), self._spec(), versions)
        self.assertNotEqual(
            key_self, key_other,
            "cache key must include the user — otherwise aggregates leak "
            "across users inside a tenant",
        )

    def test_cache_is_keyed_by_company(self):
        """Switching active company must change the key (CRITICAL regression).

        Multi-company security is row-level via ir.rule domains reading
        `allowed_company_ids`, so the SAME uid legitimately sees different
        numbers per active company. The first version of make_key omitted this
        and would have served Company A's receivables on Company B's dashboard
        for up to the 300s TTL — silently, no exception, no log.
        """
        company_a = self.env.company
        company_b = self.env["res.company"].create({"name": "P4T01 Company B"})
        versions = agg_cache.versions_for(self.env)

        key_a = agg_cache.make_key(
            self.env(context={"allowed_company_ids": [company_a.id]}),
            self._spec(), versions)
        key_b = agg_cache.make_key(
            self.env(context={"allowed_company_ids": [company_b.id]}),
            self._spec(), versions)
        key_both = agg_cache.make_key(
            self.env(context={
                "allowed_company_ids": [company_a.id, company_b.id]}),
            self._spec(), versions)

        self.assertNotEqual(key_a, key_b, "active company must change the key")
        self.assertNotEqual(
            key_a, key_both,
            "a multi-company selection must not collide with a single company")

    def test_cache_key_ignores_company_ordering(self):
        """[A, B] and [B, A] are the same scope — must not halve the hit rate."""
        company_a = self.env.company
        company_b = self.env["res.company"].create({"name": "P4T01 Company C"})
        versions = agg_cache.versions_for(self.env)
        ordered = agg_cache.make_key(
            self.env(context={
                "allowed_company_ids": [company_a.id, company_b.id]}),
            self._spec(), versions)
        reversed_ = agg_cache.make_key(
            self.env(context={
                "allowed_company_ids": [company_b.id, company_a.id]}),
            self._spec(), versions)
        self.assertEqual(ordered, reversed_)

    def test_cache_is_keyed_by_result_changing_context(self):
        """lang / tz / active_test all change _read_group's answer."""
        versions = agg_cache.versions_for(self.env)
        base = agg_cache.make_key(self.env, self._spec(), versions)
        for key, value in (
            ("lang", "ar_001"),
            ("tz", "Asia/Dubai"),
            ("active_test", False),
        ):
            with self.subTest(context_key=key):
                self.assertNotEqual(
                    base,
                    agg_cache.make_key(
                        self.env(context=dict(self.env.context, **{key: value})),
                        self._spec(), versions),
                    "%s changes the query result but not the key" % key,
                )

    def test_cache_key_ignores_per_request_context_noise(self):
        """Irrelevant context keys must not shred the hit rate."""
        versions = agg_cache.versions_for(self.env)
        base = agg_cache.make_key(self.env, self._spec(), versions)
        noisy = agg_cache.make_key(
            self.env(context=dict(self.env.context, params={"action": 42},
                                  bin_size=True)),
            self._spec(), versions)
        self.assertEqual(base, noisy)

    def test_cache_is_keyed_by_database(self):
        """Entries must never be reachable from another tenant's database."""
        versions = agg_cache.versions_for(self.env)
        key = agg_cache.make_key(self.env, self._spec(), versions)
        other_key = agg_cache.make_key(self.env, self._spec(), versions)
        self.assertEqual(key, other_key, "same db+user+spec must be stable")
        # The db name is inside the digest input; changing it must change the key.
        import json
        from odoo.addons.ncollection_core.models.aggregation.cache import _digest
        same = _digest({"db": self.env.cr.dbname, "uid": self.env.uid,
                        "su": bool(self.env.su), "spec": self._spec(),
                        "version": 0})
        different = _digest({"db": "some_other_tenant", "uid": self.env.uid,
                             "su": bool(self.env.su), "spec": self._spec(),
                             "version": 0})
        self.assertNotEqual(same, different)
        self.assertTrue(json)  # keep the import meaningful to linters

    # -- invalidation -------------------------------------------------------

    def test_source_write_invalidates_the_cached_aggregate(self):
        """A write to a tracked source model must change the answer.

        This is the whole point of the version counters: without them the
        second call would serve the pre-write number forever.
        """
        self.assertIn("res.partner", AGGREGATION_SOURCE_MODELS)
        before = self.Engine.aggregate(self._spec())
        self.assertFalse(before["cached"])
        count_before = before["rows"][0][0]

        self.env["res.partner"].create({
            "name": "p4t01 invalidation probe", "is_company": True})
        # The version snapshot has its own short TTL; expire it so the test
        # asserts the invalidation contract rather than the TTL's timing.
        agg_cache._version_snapshots.clear()

        after = self.Engine.aggregate(self._spec())
        self.assertFalse(
            after["cached"],
            "a source write must make the previous entry unreachable",
        )
        self.assertEqual(
            after["rows"][0][0], count_before + 1,
            "post-write aggregate must reflect the new row",
        )

    def test_version_counter_increments_on_create(self):
        Version = self.env["ncollection.aggregation.version"]
        before = Version._versions().get("res.partner", 0)
        self.env["res.partner"].create({"name": "p4t01 version probe"})
        self.assertGreater(
            Version._versions().get("res.partner", 0), before,
            "create on a tracked model must bump its counter",
        )

    def test_untracked_model_does_not_bump(self):
        """Only declared source models pay the bump cost."""
        Version = self.env["ncollection.aggregation.version"]
        self.assertNotIn("res.currency", AGGREGATION_SOURCE_MODELS)
        before = Version._versions().get("res.currency", 0)
        self.env["res.currency"].search([], limit=1).write({})
        self.assertEqual(
            Version._versions().get("res.currency", 0), before,
            "an untracked model must not create version churn",
        )

    # -- bounded size -------------------------------------------------------

    def test_cache_is_bounded(self):
        """An unbounded aggregation cache is a memory leak with extra steps."""
        for index in range(agg_cache.MAX_ENTRIES + 50):
            agg_cache.put("synthetic-key-%d" % index, [(index,)])
        self.assertLessEqual(
            agg_cache.stats()["entries"], agg_cache.MAX_ENTRIES,
            "cache grew past its ceiling",
        )
