# -*- coding: utf-8 -*-
"""#317: keep this module's generated SQL identifiers under the Postgres cap.

Postgres truncates/rejects identifiers over **63 characters**. Odoo derives a
many2many's relation table from the two model table names
(``sorted([a, b]) + '_rel'``) and its columns from the model names again, so a
model name that is merely *long* is fine until someone adds one more m2m — and
then the module does not install at all.

This is not hypothetical. F2-T08 (#118) hit exactly this: the first-draft model
name ``ncollection.account.report.financial.summary`` produced a 64-char
relation table and the install died with ``ValidationError: Table name ... is
too long``. The models were renamed to fit.

The guard exists so that failure arrives as a named test with the arithmetic in
the message, rather than as a cryptic install error someone has to reverse-
engineer. It walks the LIVE registry rather than parsing source, so it accounts
for inherited fields and whatever Odoo actually generated.
"""
from odoo.tests import TransactionCase, tagged

# Postgres' hard limit (NAMEDATALEN - 1).
_PG_IDENTIFIER_MAX = 63

# The measured worst case on develop today: General Ledger's journal/account
# relation tables, both 61 of 63. This is a RATCHET, like the pylint baseline in
# scripts/ci/pylint_gate.sh — lower it when the margin improves, never raise it.
_TIGHTEST_BASELINE = 61

# Models this module owns. Scoped deliberately: a registry-wide sweep would
# make THIS module's suite fail for another team's model, and ownership of the
# fix would be unclear.
_PREFIX = 'ncollection.account.report'


@tagged('post_install', '-at_install')
class TestIdentifierLimits(TransactionCase):

    def _report_models(self):
        return sorted(name for name in self.env.registry
                      if name.startswith(_PREFIX))

    def test_no_generated_identifier_exceeds_the_postgres_limit(self):
        """The hard failure: over 63 chars, the module will not install."""
        offenders = []
        for model_name in self._report_models():
            model = self.env[model_name]
            for field in model._fields.values():
                if field.type != 'many2many':
                    continue
                for label, identifier in (('relation', field.relation),
                                          ('column1', field.column1),
                                          ('column2', field.column2)):
                    if identifier and len(identifier) > _PG_IDENTIFIER_MAX:
                        offenders.append(
                            "%s.%s %s=%s (%d chars)"
                            % (model_name, field.name, label, identifier,
                               len(identifier)))
        self.assertEqual(
            offenders, [],
            "generated identifier(s) over Postgres' %d-char limit — the module "
            "will not install. Give the field an explicit shorter `relation=` "
            "(per MODEL, never shared: two models on one relation table would "
            "collide on transient ids), or shorten the model name.\n  %s"
            % (_PG_IDENTIFIER_MAX, '\n  '.join(offenders)))

    def test_identifier_headroom_does_not_get_worse(self):
        """A ratchet, in the same spirit as scripts/ci/pylint_gate.sh's baseline.

        The hard test above only fires once the module is ALREADY broken — at
        which point the install fails anyway and the guard has bought only a
        clearer message. This one fires while there is still room to act.

        ``_TIGHTEST_BASELINE`` is the measured worst case on develop today
        (General Ledger's ``account_journal_…_general_ledger_rel`` /
        ``account_account_…_general_ledger_rel``, both 61 of 63). Adding an m2m
        to the engine, or lengthening a model name, pushes past it and fails
        here — 2 characters before it would fail at install time instead.

        **Lower this number when the margin improves; never raise it.** Raising
        it is how a ratchet becomes a rubber stamp.
        """
        widths = sorted(
            ((len(field.relation), field.relation, model_name, field.name)
             for model_name in self._report_models()
             for field in self.env[model_name]._fields.values()
             if field.type == 'many2many' and field.relation),
            reverse=True)
        self.assertTrue(widths, "no many2many fields found — guard is vacuous")
        worst_len, worst_name, worst_model, worst_field = widths[0]
        self.assertLessEqual(
            worst_len, _TIGHTEST_BASELINE,
            "identifier headroom got WORSE: %s.%s generates %s (%d chars, "
            "baseline %d, Postgres cap %d). Give that field an explicit shorter "
            "`relation=` — per MODEL, never shared, since two models on one "
            "relation table would collide on transient ids."
            % (worst_model, worst_field, worst_name, worst_len,
               _TIGHTEST_BASELINE, _PG_IDENTIFIER_MAX))
