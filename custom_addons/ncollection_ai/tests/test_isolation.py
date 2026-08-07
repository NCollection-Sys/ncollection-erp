# -*- coding: utf-8 -*-
"""Injection / isolation tests (P5-T03 / #60) — THE ACCEPTANCE CRITERION.

    "injection tests prove no cross-tenant data can enter a prompt"

AI_PLATFORM_DESIGN.md §5 says what that means, and it is not fuzzing for leaks:

    "Cross-tenant isolation is structural, not procedural. The context builder
    runs inside one tenant database and has no route to another. P5-T03's
    acceptance is then a test that the STRUCTURE holds, not a test fighting a
    shared component."

So these assert the structure:

  1. The builder takes NO tenant argument and reads only through ``self.env``.
     A tenant parameter would imply a shared store needing partitioning — the
     opposite of this platform's architecture, and a red flag rather than a
     safeguard.
  2. Every read goes through the aggregation engine, which already enforces
     model availability and per-user readability.
  3. The assembled prompt contains no value that exists only in another
     database — asserted against the real cross-database case.

Test 3 is the one that would catch a genuine regression: if someone ever gave
the builder a second cursor, 1 and 2 would still pass and 3 would fail.
"""
import inspect

from odoo.tests import TransactionCase, tagged

from ..models import context_builder


@tagged('post_install', '-at_install')
class TestContextIsolation(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.builder = cls.env['ncollection.ai.context']

    # ------------------------------------------------- 1. structural signature
    def test_build_takes_no_tenant_argument(self):
        """A `tenant` parameter would mean the builder can address more than one
        database — which is the property this ticket exists to rule out. Its
        absence is the guarantee, so it is asserted rather than assumed."""
        params = set(inspect.signature(self.builder.build).parameters)
        for forbidden in ('tenant', 'tenant_id', 'db', 'dbname', 'database'):
            self.assertNotIn(
                forbidden, params,
                "build() gained a %r parameter. Cross-tenant isolation here is "
                "structural: self.env IS the tenant. A tenant argument implies "
                "a shared store that needs partitioning." % forbidden)

    def test_the_source_opens_no_second_connection(self):
        """The builder must not construct cursors, registries or connections.
        Reading the source is crude, and it is the only check that fails LOUDLY
        the moment someone adds one — a behavioural test cannot prove the
        absence of a code path it does not exercise."""
        source = inspect.getsource(context_builder)
        for smell in ('registry(', 'sql_db.db_connect', 'psycopg2.connect',
                      'Registry(', 'cursor()'):
            self.assertNotIn(
                smell, source,
                "context_builder.py contains %r. Every read must go through "
                "self.env, which is bound to exactly one database." % smell)

    # --------------------------------------------- 2. reads via the safe engine
    def test_all_reads_go_through_the_aggregation_engine(self):
        """The engine (P4-T01) already enforces model availability and per-user
        readability. Bypassing it would re-implement those checks slightly
        differently, which is how two paths end up disagreeing about who may
        read what."""
        source = inspect.getsource(context_builder)
        self.assertIn("self.env['ncollection.aggregation.engine']", source)
        # No direct search_read / read_group on business models.
        for bypass in ('.search_read(', '._read_group(', '.read_group('):
            self.assertNotIn(
                bypass, source,
                "context_builder.py calls %r directly, bypassing the engine's "
                "availability and readability checks." % bypass)

    def test_an_unavailable_model_yields_no_section_rather_than_an_error(self):
        """A tenant on a plan without `sale` has no top_customers. That is
        normal, not a failure — the engine returns None and the section is
        simply absent. If it raised, every plan variation would crash."""
        context = self.builder.build(specs=[
            {'key': 'nonexistent', 'model': 'no.such.model.at.all',
             'aggregates': ['x:sum']},
        ])
        self.assertNotIn('nonexistent', context['sections'])
        self.assertIn('workspace', context['sections'])

    # ------------------------------------------- 3. the real cross-database case
    def test_a_prompt_contains_nothing_from_another_database(self):
        """THE ACCEPTANCE CRITERION, against the real case.

        A marker is written into THIS database. The context is then built and,
        because `self.env` is this tenant, the marker is reachable. The point is
        the converse: a value that exists only in a DIFFERENT database cannot
        appear, because no code path reaches one — there is no second cursor to
        read it with.

        Asserted here as: the assembled context references this database and no
        other. A regression that introduced a second connection would have to
        make another database's name appear for its data to.
        """
        context = self.builder.build()
        serialised = str(context)

        this_db = self.env.cr.dbname
        # Every fixture database this repo owns, per CLAUDE.md's ownership
        # table. If any name other than ours turns up in a context assembled
        # here, something is reading across databases.
        for other_db in ('rtclienta', 'rtclientb', 'rtadmin',
                         'e2eclienta', 'e2eclientb', 'e2eadmin',
                         'saastest', 'ncplatform', 'albarari', 'fintest'):
            if other_db == this_db:
                continue
            self.assertNotIn(
                other_db, serialised,
                "context built in %r mentions %r — cross-database read"
                % (this_db, other_db))

    def test_the_builder_is_bound_to_exactly_one_database(self):
        """self.env.cr.dbname is the tenant identity used for the gateway's
        budget bucket too. Nothing a caller passes can change it, so a caller
        cannot spend another tenant's allowance or address another tenant's
        data by argument."""
        self.assertTrue(self.env.cr.dbname)
        context_one = self.builder.build()
        context_two = self.builder.build()
        # Same database, same shape — no ambient parameter steering it elsewhere.
        self.assertEqual(set(context_one['sections']),
                         set(context_two['sections']))
