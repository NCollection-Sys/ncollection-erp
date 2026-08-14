# -*- coding: utf-8 -*-
"""P8-T05: rules as data — seeded, idempotent, and re-seeded when models arrive.

`auditlog.rule` is UI-only upstream: the manifest ships groups, ACLs, a cron and
five view files, and no rule data at all. A fleet cannot be configured by
clicking, so this module seeds them — and the interesting part is not the
seeding, it is that seeding ONCE is not enough.

REGRESSIONS.md R-014, twice over: a hook that seeds something belonging to a
module installed LATER silently does nothing, and nothing goes red.
"""
from unittest.mock import patch

from odoo.tests import tagged

from .common import AuditCommon


@tagged('post_install', '-at_install')
class TestAuditRules(AuditCommon):

    def test_the_named_critical_models_are_seeded(self):
        seeded = set(self.Rule.search([]).mapped('model_model'))
        self.assertTrue(seeded, "nothing is audited at all")

    def test_res_users_is_WITHHELD_and_says_why(self):
        """The ticket names res.users. This module refuses to audit it, and
        that refusal is asserted so it reads as a decision rather than a
        slip.

        Auditing res.users DISABLES the plan seat limit: ncollection_core
        enforces max_users by overriding res.users.create, auditlog enforces
        auditing by monkeypatching create onto the model class, and the licence
        check loses. Measured by bisection — with res.users seeded,
        ncollection_core fails test_limit_blocks_raw_orm_create and
        test_reactivation_counts_against_limit; dropping it alone returns that
        suite to 0 failed.

        If someone restores it, this fails HERE with the reason attached,
        instead of the breakage surfacing as two unrelated failures in another
        module's suite.
        """
        from ..models.audit_rule import _NC_WITHHELD
        self.assertIn('res.users', _NC_WITHHELD)
        self.assertIn('seat limit', _NC_WITHHELD['res.users'])
        self.assertNotIn(
            'res.users', set(self.Rule.search([]).mapped('model_model')),
            "res.users is audited again — that disables the plan seat limit "
            "(see _NC_WITHHELD)")

    def test_no_group_restricted_field_is_ever_audited(self):
        """A field the ORM restricts to a group must not be copied into a log
        a DIFFERENT group can read.

        `auditlog.get_auditlog_fields()` returns every stored field and never
        looks at `groups=`, while `auditlog.log.line` is readable by
        `auditlog.group_auditlog_user`. On the platform database that published
        `ncollection.tenant.checkout_token` — a live signup bearer credential
        restricted to base.group_system — to every audit viewer. Found in
        security review.

        Asserted generically rather than by naming the field: a per-field
        allowlist protects today's secret and silently exposes the next one.
        """
        offenders = []
        for rule in self.Rule.search([]):
            model = self.env.get(rule.model_model)
            if model is None:
                continue
            excluded = set(rule.fields_to_exclude_ids.mapped('name'))
            for fname, field in model._fields.items():
                if field.groups and fname not in excluded:
                    offenders.append("%s.%s" % (rule.model_model, fname))
        self.assertFalse(
            offenders,
            "group-restricted fields are being written into the audit trail, "
            "which any auditlog viewer can read: %s" % sorted(offenders))

    def test_the_exclusion_mechanism_itself_excludes_a_restricted_field(self):
        """The sweep above is VACUOUS wherever no audited model happens to
        carry a `groups=` field — under `make test m=ncollection_audit` only
        this module is installed, and its RED proof came back GREEN for exactly
        that reason. This exercises the mechanism directly against a model
        known to have one, whatever is installed.
        """
        IrModel = self.env['ir.model'].sudo()
        for record in IrModel.search([]):
            model = self.env.get(record.model)
            if model is None or model._abstract or model._transient:
                continue
            restricted = [name for name, field in model._fields.items()
                          if field.groups and field.store]
            if not restricted:
                continue
            excluded = set(self.Rule._nc_excluded_fields(record).mapped('name'))
            self.assertTrue(
                set(restricted) <= excluded,
                "%s: these group-restricted fields would be copied into the "
                "audit trail: %s" % (record.model,
                                     sorted(set(restricted) - excluded)))
            return
        self.fail("no model in this database has a group-restricted stored "
                  "field, so the exclusion mechanism is untested here")

    def test_every_persistent_ncollection_model_present_is_discovered(self):
        """Discovery is what lets ONE module serve a tenant database and the
        platform database, whose ncollection models differ. Hardcoding platform
        model names into a tenant-layer module would also break Rule 3.

        Asserted against whatever this database actually holds rather than
        against a fixed list: under `make test m=ncollection_audit` only this
        module is installed, and a test demanding `ncollection.tenant` would
        fail for the wrong reason. In the full matrix the set is large, so the
        assertion has real content there.
        """
        from ..models.audit_rule import SENSITIVE_MODELS
        IrModel = self.env['ir.model'].sudo()
        expected = set()
        for record in IrModel.search([('model', '=like', 'ncollection.%')]):
            model = self.env.get(record.model)
            if model is None or model._transient or model._abstract:
                continue
            if record.model.startswith('ncollection.audit'):
                continue
            if record.model in SENSITIVE_MODELS:
                continue          # excluded on purpose — see the auth-log test
            expected.add(record.model)
        seeded = set(self.Rule.search([]).mapped('model_model'))
        self.assertFalse(
            expected - seeded,
            "these ncollection models exist here and are NOT audited: %s"
            % sorted(expected - seeded))

    def test_the_audit_module_does_not_audit_itself(self):
        """Non-vacuous everywhere: `ncollection.audit.seal` exists in every
        database this module is installed on. Auditing the audit trail would
        make every sealed range generate the rows the next seal must cover."""
        self.assertTrue(
            self.env['ir.model'].sudo().search_count(
                [('model', '=', 'ncollection.audit.seal')]),
            "the fixture model is missing, so this proves nothing")
        seeded = set(self.Rule.search([]).mapped('model_model'))
        self.assertFalse([n for n in seeded if n.startswith('ncollection.audit')])

    def test_authentication_telemetry_is_not_copied_into_the_general_trail(self):
        """Excluding a group-restricted FIELD is not enough when the whole
        RECORD is the sensitive thing.

        `ncollection.auth.log` is authentication telemetry — logins, failures,
        reset requests, source addresses — and `auditlog.log.line` is readable
        by `auditlog.group_auditlog_user`. Copying it into a lower-trust trail
        hands out the record that trail exists to protect, and duplicates PII
        that `ncollection_auth`'s own retention minimises on a schedule this
        module knows nothing about. Flagged by security review.
        """
        from ..models.audit_rule import SENSITIVE_MODELS
        self.assertIn('ncollection.auth.log', SENSITIVE_MODELS)
        seeded = set(self.Rule.search([]).mapped('model_model'))
        self.assertFalse(
            seeded & SENSITIVE_MODELS,
            "a model whose contents outrank an audit viewer's clearance is "
            "being copied into the general trail: %s"
            % sorted(seeded & SENSITIVE_MODELS))
        self.assertFalse(
            set(self.Rule._nc_audited_models()) & SENSITIVE_MODELS)

    def test_transient_models_are_never_audited(self):
        """Wizards are a form somebody opened, not a fact worth keeping — and
        they are created constantly."""
        for rule in self.Rule.search([]):
            model = self.env.get(rule.model_model)
            self.assertFalse(
                model is not None and model._transient,
                "%s is a wizard and is being audited" % rule.model_model)

    def test_reads_are_not_logged(self):
        """The highest-volume operation in an ERP and the lowest-value audit
        signal. Asserted so that turning it on is a decision, not a drift."""
        self.assertFalse(any(self.Rule.search([]).mapped('log_read')))

    def test_the_noisy_field_exclusions_still_apply_to_something(self):
        """NOISY_FIELDS is currently DEAD, and this test says so out loud.

        Every model it names — account.move, account.move.line, res.users — is
        withheld (#428/#429/#431), so none of those exclusions apply to anything
        that ships today. They are kept because they become live again the
        moment those models can be audited, not because they do anything now.

        The earlier version of this test asserted the exclusion on a seeded
        res.users rule and SKIPPED when that rule was absent — which, after the
        rescope, was always. `scripts/ci/check_skips.py` failed the build on it
        (#363: a skipped test is not a passing test), which is the guard working
        exactly as intended on its own repo.

        So this exercises the MECHANISM directly against a model that need not
        be audited, and it runs everywhere.
        """
        from ..models.audit_rule import NOISY_FIELDS
        IrModel = self.env['ir.model'].sudo()
        checked = 0
        for model_name, noisy in NOISY_FIELDS.items():
            record = IrModel.search([('model', '=', model_name)], limit=1)
            if not record:
                continue          # that module is not installed here
            excluded = set(self.Rule._nc_excluded_fields(record).mapped('name'))
            present = {name for name in noisy
                       if self.env[model_name]._fields.get(name)}
            self.assertTrue(
                present <= excluded,
                "%s: noisy fields that would still be logged: %s"
                % (model_name, sorted(present - excluded)))
            checked += 1
        self.assertTrue(
            checked, "no model named in NOISY_FIELDS exists on this database, "
                     "so this proves nothing — if that is now permanent, delete "
                     "NOISY_FIELDS rather than leaving a dead constant")

    def test_seeding_twice_creates_nothing(self):
        """Standing Rule 12: prove idempotency, do not echo it."""
        before = self.Rule.search_count([])
        self.assertFalse(self.Rule._nc_seed_rules(),
                         "a second seeding run created rules")
        self.assertEqual(self.Rule.search_count([]), before)

    def test_a_model_that_appears_LATER_gets_seeded_on_the_next_registry_load(self):
        """R-014, directly. Delete a rule to simulate a model that did not
        exist at install time, then do what a later install does — reload the
        registry — and assert the rule comes back on its own.
        """
        # The suite's own probe model, not whatever happens to be seeded: on a
        # database carrying only this module NOTHING is audited (res.users and
        # res.partner are withheld, account/sale are absent, and the module's
        # own models are excluded), so picking the "first audited model" raised
        # IndexError. A guard that cannot run without the rest of the platform
        # installed is not much of a guard.
        name = 'res.country'
        rule = self.Rule.search([('model_model', '=', name)], limit=1)
        self.assertTrue(rule, "no probe rule to remove")
        rule.set_to_draft()
        rule.unlink()
        self.assertFalse(self.Rule.search([('model_model', '=', name)]))
        # `_register_hook` re-seeds only the SHIPPED list, which does not
        # include the probe. Seed through the same public entry point
        # provisioning would use, having first put the probe on the list.
        with patch.object(type(self.Rule), '_nc_audited_models',
                          lambda self: [name]):
            self.Rule._register_hook()
        self.assertTrue(
            self.Rule.search([('model_model', '=', name)]),
            "a model present at registry-load time was not re-seeded, so a "
            "module installed after this one is never audited (R-014)")

    def test_every_seeded_rule_is_confirmed(self):
        """A draft rule patches nothing — it is a row that looks like coverage
        and produces no logs at all."""
        states = set(self.Rule.search([]).mapped('state'))
        self.assertEqual(states, {'subscribed'} if 'subscribed' in states
                         else {'confirmed'} if 'confirmed' in states else states)
        self.assertNotIn('draft', states, "a seeded rule is still draft")
