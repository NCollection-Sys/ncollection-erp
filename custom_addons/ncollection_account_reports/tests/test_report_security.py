# -*- coding: utf-8 -*-
"""#413: every report this module grants is private to the user who ran it.

F2-T05 shipped five models with an ACL row and no ``ir.rule``. Both wizards
filter ``create_uid`` in PYTHON when clearing a previous run — and that hand
filter was the only control, so a plain RPC ``search([])`` on
``partner.line``/``aged.line`` returned another accountant's rendered partner
figures. Standing Rule 4: a restriction that lives only above the ORM is not a
restriction.

TWO TESTS, DELIBERATELY DIFFERENT IN KIND.

``test_a_second_accountant_cannot_read_another_users_run`` is BEHAVIOURAL: it
runs each report as one user and searches as another. It proves the effect, on
the five models that were broken.

``test_every_model_this_module_grants_is_scoped_to_its_creator`` is the GUARD:
it walks this module's own ``ir.model.access`` rows at runtime and demands a
matching rule for each. It is what makes a SIXTH model impossible to add
without one — which is the actual failure here, twice over (F2-T05, then F2-T04
until review caught it). Being a runtime check rather than a static parse, it
also fails if a rule exists in XML but never loaded.

It is scoped to THIS MODULE on purpose. A repo-wide "every ACL row needs an
ir.rule" check would be wrong — most models legitimately have none — and a
guard that cries wolf is a guard people learn to skip.
"""
from datetime import date

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged

MODULE = 'ncollection_account_reports'


@tagged('post_install', '-at_install')
class TestReportIsolation(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.receivable = cls.company_data['default_account_receivable']
        cls.revenue = cls.company_data['default_account_revenue']
        cls.journal = cls.company_data['default_journal_misc']
        # A posted receivable so the partner reports render actual ROWS. With an
        # empty ledger the "other user sees nothing" assertion would hold
        # because there is nothing to see — the test would pass without the
        # rules existing at all.
        move = cls.env['account.move'].create({
            'move_type': 'entry', 'journal_id': cls.journal.id,
            'date': date(2026, 3, 10),
            'line_ids': [
                (0, 0, {'account_id': cls.receivable.id, 'debit': 1000.0,
                        'credit': 0.0, 'partner_id': cls.partner_a.id}),
                (0, 0, {'account_id': cls.revenue.id, 'debit': 0.0,
                        'credit': 1000.0, 'partner_id': cls.partner_a.id}),
            ]})
        move.action_post()

        cls.date_from = date(2026, 1, 1)
        cls.date_to = date(2026, 12, 31)
        cls.accountant = cls.env['res.users'].create({
            'name': 'Second Accountant', 'login': 'nc413_second',
            'group_ids': [(6, 0, [
                cls.env.ref('account.group_account_readonly').id,
                cls.env.ref('base.group_user').id])]})

    def _values(self, **overrides):
        values = {'date_from': self.date_from, 'date_to': self.date_to,
                  'target_move': 'posted'}
        values.update(overrides)
        return values

    def test_a_second_accountant_cannot_read_another_users_run(self):
        """The five models #413 is about, proved by effect rather than by
        reading the configuration back."""
        cases = [
            ('ncollection.account.report.partner.ledger',
             'ncollection.account.report.partner.line', {}),
            ('ncollection.account.report.aged.partner',
             'ncollection.account.report.aged.line', {}),
            ('ncollection.account.report.statement', None, {}),
        ]
        for wizard_model, line_model, extra in cases:
            with self.subTest(model=wizard_model):
                wizard = self.env[wizard_model].create(self._values(**extra))
                self.assertFalse(
                    self.env[wizard_model].with_user(self.accountant).search(
                        [('id', '=', wizard.id)]),
                    "another accountant could read someone else's %s run"
                    % wizard_model)
                if line_model is None:
                    continue
                wizard.action_view()
                Line = self.env[line_model]
                mine = Line.search([])
                self.assertTrue(
                    mine, "%s rendered no rows, so this proves nothing — the "
                          "fixture is not producing data" % line_model)
                self.assertFalse(
                    Line.with_user(self.accountant).search([]),
                    "another accountant could read someone else's rendered "
                    "%s rows" % line_model)

    def test_every_model_this_module_grants_is_scoped_to_its_creator(self):
        """THE GUARD. A sixth model must not be addable without a rule.

        Walks this module's own ``ir.model.access`` rows rather than a
        hardcoded list, so it covers models that do not exist yet. ``sudo()``
        because it inspects security CONFIGURATION — the behavioural test above
        is what exercises the boundary itself.
        """
        Data = self.env['ir.model.data'].sudo()
        access_ids = Data.search([
            ('module', '=', MODULE),
            ('model', '=', 'ir.model.access')]).mapped('res_id')
        accesses = self.env['ir.model.access'].sudo().browse(access_ids)
        self.assertTrue(accesses, "no ir.model.access rows found for %s — this "
                                  "guard is aimed at the wrong module and is "
                                  "verifying nothing" % MODULE)

        Rule = self.env['ir.rule'].sudo()
        unscoped = []
        for model in accesses.mapped('model_id'):
            rules = Rule.search([('model_id', '=', model.id),
                                 ('global', '=', True)])
            if not any('create_uid' in (rule.domain_force or '')
                       for rule in rules):
                unscoped.append(model.model)
        self.assertFalse(
            sorted(unscoped),
            "these models are granted to account.group_account_readonly with "
            "no global ir.rule scoping them to create_uid, so any such user "
            "can read another user's report run or its rendered figures over "
            "RPC. Add a rule_<report>_own record to report_security.xml — a "
            "create_uid filter written in Python is not a control (#413).")
