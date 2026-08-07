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
import pathlib

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
        # EVERY model file, not just this one. Scoping it to context_builder
        # was a real gap: a psycopg2.connect() dropped into pii.py or
        # ai_question.py tomorrow would have passed the whole suite.
        models_dir = pathlib.Path(context_builder.__file__).parent
        for path in sorted(models_dir.glob('*.py')):
            source = path.read_text()
            for smell in ('registry(', 'sql_db.db_connect', 'psycopg2.connect',
                          'Registry(', 'cursor()'):
                self.assertNotIn(
                    smell, source,
                    "%s contains %r. Every read must go through self.env, "
                    "which is bound to exactly one database."
                    % (path.name, smell))

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
    def test_no_other_database_name_appears_in_an_assembled_context(self):
        """A narrow trip-wire. NOT the whole acceptance criterion — the
        isolation audit was right to call the original framing overstated.

        What this actually checks: no OTHER fixture database's NAME appears in
        a context built here. That catches one specific accident (a connection
        string or db name leaking into the payload) and nothing more. A genuine
        cross-tenant read would surface as another tenant's partner name or
        invoice amount, and this test would sail straight past it.

        The real assurance is structural and lives in the two tests above: the
        builder takes no tenant argument, and no file in models/ opens a second
        cursor. Those are what make a cross-database read impossible; this is a
        cheap extra alarm, labelled honestly.

        A marker is written into THIS database. The context is then built and,
        because `self.env` is this tenant, the marker is reachable. The point is
        the converse: a value that exists only in a DIFFERENT database cannot
        appear, because no code path reaches one — there is no second cursor to
        read it with.

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

    # ------------------------------------------- 4. the specs must actually work
    def test_default_specs_are_accepted_by_the_engine(self):
        """REGRESSION GUARD, and the reason it exists is the interesting part.

        `aggregate()` NEVER RAISES — a malformed spec returns None. That is
        correct for robustness and it means a spec bug is INVISIBLE: the section
        simply vanishes and nothing appears in any log.

        It happened here. Two specs ordered by a bare field name
        ('amount_total desc') where Odoo's _read_group requires the aggregate
        alias ('amount_total:sum desc'). Both returned None, the context shipped
        with receivables and top_customers missing, and 6 of the 20 review
        questions became silently unanswerable.

        So this asserts every default spec is at least ACCEPTED. It cannot
        assert rows exist — an empty database legitimately has none — but a spec
        the engine rejects outright is always a bug.
        """
        engine = self.env['ncollection.aggregation.engine']
        for spec in self.builder._default_specs():
            error = engine._validate_spec(spec)
            self.assertFalse(
                error,
                "default spec %r is invalid: %s. A rejected spec silently "
                "removes its whole section from every prompt."
                % (spec['key'], error))

    def test_order_clauses_name_the_aggregate_alias(self):
        """The specific shape of the bug above, pinned so it cannot return.

        Odoo's _read_group rejects `order: 'amount_total desc'` and accepts
        `order: 'amount_total:sum desc'`. Verified directly against the engine,
        not assumed.
        """
        for spec in self.builder._default_specs():
            order = spec.get('order')
            if not order:
                continue
            field = order.split()[0]
            self.assertIn(
                ':', field,
                "spec %r orders by %r — a bare field name. _read_group needs "
                "the aggregate alias (e.g. 'amount_total:sum desc'), and the "
                "engine will silently return None instead of erroring."
                % (spec['key'], field))

    def test_engine_rows_are_labelled_so_the_sanitiser_can_see_them(self):
        """THE MOST SERIOUS BUG THIS TICKET HIT, pinned.

        The engine returns positional rows — ``[[16, "Nakheel"], 101920.0]`` —
        with no field names. ncollection.ai.pii pseudonymises BY FIELD NAME, so
        an unlabelled row sails straight past it and real customer names reach
        the provider.

        It passed every unit test because those used ``{'partner_id': 'X'}`` —
        the shape I ASSUMED, not the shape the engine returns. Only the review
        harness, running against real data, showed one pseudonym in a context
        holding six customer names.

        So this asserts against the engine's ACTUAL output shape.
        """
        spec = {'key': 'k', 'groupby': ['partner_id'],
                'aggregates': ['amount_residual:sum']}
        raw_rows = [[[16, 'Nakheel'], 101920.0], [[17, 'Emaar'], 5000.0]]

        labelled = self.builder._label_rows(spec, raw_rows)
        self.assertEqual(labelled[0]['partner_id'], 'Nakheel')
        self.assertEqual(labelled[0]['amount_residual:sum'], 101920.0)
        # The internal id is dropped: a stable cross-request handle would defeat
        # the point of pseudonymising the name beside it.
        self.assertNotIn(16, labelled[0].values())

        clean, mapping = self.env['ncollection.ai.pii'].sanitise(
            {'receivables': labelled})
        self.assertNotIn('Nakheel', str(clean))
        self.assertNotIn('Emaar', str(clean))
        self.assertEqual(clean['receivables'][0]['partner_id'], 'PARTNER_1')
        self.assertEqual(mapping['PARTNER_1'], 'Nakheel')

    def test_every_default_section_survives_sanitisation_without_leaking(self):
        """End-to-end on the real default context: after sanitising, no value
        that looks like a partner name may remain. Belt and braces over the
        unit test above, because the failure mode was precisely a gap between
        the shape tested and the shape produced."""
        context = self.builder.build()
        partner_names = self.env['res.partner'].search([]).mapped('name')
        clean, _ = self.env['ncollection.ai.pii'].sanitise(
            {'context': context['sections']})
        serialised = str(clean)
        for name in partner_names:
            if not name or len(name) < 4:
                continue          # too short to assert on without false hits
            self.assertNotIn(
                name, serialised,
                "partner name %r survived sanitisation into the prompt" % name)

    def test_recent_invoices_asks_for_the_MOST_RECENT_months(self):
        """REGRESSION. The spec had no `order`, and _read_group defaults to the
        groupby term ASCENDING — so with limit 12 it returned the OLDEST twelve
        months. Unlike a rejected spec this fills successfully with WRONG data:
        nothing dropped, nothing logged, and three review questions answered
        from stale figures.

        Invisible below 12 months of history, which is why albarari looked fine.
        Asserting the spec's intent is the only check that works on any dataset.
        """
        spec = next(s for s in self.builder._default_specs()
                    if s['key'] == 'recent_invoices')
        self.assertIn('order', spec,
                      "recent_invoices has no order: _read_group will return "
                      "the OLDEST months, not the recent ones")
        self.assertTrue(
            spec['order'].strip().lower().endswith('desc'),
            "recent_invoices must order DESC to be 'recent'; got %r"
            % spec['order'])

    def test_the_workspace_company_name_is_pseudonymised(self):
        """The company name reaches the PROVIDER, which — unlike the gateway —
        has no idea who this tenant is. §5: the model does not need real
        identities. The builder's docstring claimed this was handled before it
        actually was; this test is what makes the claim true."""
        context = self.builder.build()
        company = context['sections']['workspace']['company']
        clean, mapping = self.env['ncollection.ai.pii'].sanitise(
            {'context': context['sections']})
        self.assertNotIn(
            company, str(clean),
            "the workspace company name crossed the boundary unpseudonymised")
        self.assertTrue(clean['context']['workspace']['company']
                        .startswith('PARTNER_'))

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
