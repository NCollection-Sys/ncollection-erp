# -*- coding: utf-8 -*-
"""F4-T02: budget lifecycle, the pro-rata rule, and Budget vs Actual.

THE FIGURE THAT MATTERS is the variance, and it is compared against an actual
computed independently — `_nc_actuals` reads `account.move.line` and knows
nothing about budgets. Every expected number is worked out by hand in the
comment beside it.

THE PRO-RATA RULE gets its own tests because it is this ticket's single
judgement call: FPA does not say what happens when a budget's span and the
reporting window only partly overlap, so the choice (by day overlap, inclusive
both ends) is asserted rather than assumed.
"""
from datetime import date

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestBudget(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Budget = cls.env['ncollection.account.budget']
        cls.Report = cls.env['ncollection.account.report.budget.actual']
        cls.expense = cls.company_data['default_account_expense']
        cls.payable = cls.company_data['default_account_payable']
        cls.journal = cls.company_data['default_journal_misc']

        # A 2026 budget: 12,000 of expense across the year.
        cls.budget = cls.Budget.create({
            'name': '2026 Operating Budget',
            'date_from': date(2026, 1, 1),
            'date_to': date(2026, 12, 31),
            'line_ids': [(0, 0, {'account_id': cls.expense.id,
                                 'amount': 12000.0})],
        })

        def spend(day, amount):
            move = cls.env['account.move'].create({
                'move_type': 'entry', 'journal_id': cls.journal.id, 'date': day,
                'line_ids': [
                    (0, 0, {'account_id': cls.expense.id, 'debit': amount,
                            'credit': 0.0}),
                    (0, 0, {'account_id': cls.payable.id, 'debit': 0.0,
                            'credit': amount}),
                ]})
            move.action_post()
            return move

        cls.spend = staticmethod(spend)
        spend(date(2026, 3, 10), 1500.0)   # March: 1,500 actual

    def _report(self, **overrides):
        values = {'date_from': date(2026, 3, 1), 'date_to': date(2026, 3, 31),
                  'target_move': 'posted', 'comparison_type': 'none'}
        values.update(overrides)
        return self.Report.create(values)

    # ---- lifecycle -------------------------------------------------------

    def test_the_lifecycle_is_draft_then_approved_then_revised(self):
        self.assertEqual(self.budget.state, 'draft')
        self.budget.action_approve()
        self.assertEqual(self.budget.state, 'approved')
        revision = self.budget.action_revise()
        self.assertEqual(self.budget.state, 'revised')
        self.assertEqual(revision.state, 'draft')
        self.assertEqual(revision.revised_from_id, self.budget,
                         "the revision does not point at what it supersedes, "
                         "so the audit chain is broken")

    def test_a_revision_keeps_the_original(self):
        """A budget that was approved and acted on is a record of what was
        decided. Overwriting it in place would destroy the audit history the
        acceptance asks for."""
        self.budget.action_approve()
        revision = self.budget.action_revise()
        self.assertTrue(self.budget.exists())
        self.assertEqual(len(self.budget.line_ids), 1,
                         "the original's lines were altered by the revision")
        self.assertNotEqual(revision.id, self.budget.id)

    def test_an_empty_budget_cannot_be_approved(self):
        """Approving nothing is not an approval."""
        empty = self.Budget.create({
            'name': 'Empty', 'date_from': date(2026, 1, 1),
            'date_to': date(2026, 12, 31)})
        with self.assertRaises(UserError):
            empty.action_approve()

    def test_only_a_draft_can_be_approved_and_only_approved_revised(self):
        with self.assertRaises(UserError):
            self.budget.action_revise()          # still draft
        self.budget.action_approve()
        with self.assertRaises(UserError):
            self.budget.action_approve()         # already approved

    def test_a_superseded_budget_cannot_be_reopened(self):
        """Two live budgets for one period is worse than none."""
        self.budget.action_approve()
        self.budget.action_revise()
        with self.assertRaises(UserError):
            self.budget.action_reset_to_draft()

    def test_a_revision_carries_the_lines_forward(self):
        """THE test that was missing, and it is a CRITICAL one.

        Odoo's One2many defaults to ``copy=False``, so ``action_revise`` used to
        return a correct header — right name, dates, ``revised_from_id`` — with
        ZERO lines, silently. "Revise" means "start from what was approved and
        adjust it"; without the lines there is nothing to adjust.

        The old test only asserted the ORIGINAL kept its line, which stayed true
        the whole time the revision was coming out empty.
        """
        self.budget.action_approve()
        revision = self.budget.action_revise()
        self.assertEqual(
            len(revision.line_ids), len(self.budget.line_ids),
            "the revision lost the lines it was supposed to start from")
        self.assertEqual(revision.line_ids.account_id, self.expense)
        self.assertAlmostEqual(revision.line_ids.amount, 12000.0, places=2)
        # ...and it is a COPY, not the same rows moved across.
        self.assertNotEqual(revision.line_ids.id, self.budget.line_ids.id)

    def test_an_approved_budget_stops_accepting_line_changes(self):
        """Otherwise 'approved' is a label, not a state, and the audit history
        records an approval of something that later changed."""
        self.budget.action_approve()
        with self.assertRaises(ValidationError):
            self.env['ncollection.account.budget.line'].create({
                'budget_id': self.budget.id,
                'account_id': self.expense.id, 'amount': 500.0})

    def test_an_approved_budget_refuses_edits_to_an_EXISTING_line(self):
        """The path the first guard missed entirely.

        Odoo runs a constraint only when one of its declared fields is written,
        so a guard constrained on ``budget_id`` alone fired on create and never
        on ``line.write({'amount': ...})``. The README and the acceptance
        criterion both claimed approved budgets were immutable while an RPC
        write sailed straight through.
        """
        self.budget.action_approve()
        line = self.budget.line_ids[0]
        for values in ({'amount': 99999.0},
                       {'account_id': self.payable.id},
                       {'name': 'quietly changed'}):
            with self.subTest(field=list(values)[0]):
                with self.assertRaises(ValidationError):
                    line.write(values)

    def test_an_approved_budget_refuses_line_DELETION(self):
        """`@api.constrains` never fires on delete, so the constraint above
        cannot cover this however many fields it lists — deleting a line was the
        one edit that stayed possible."""
        self.budget.action_approve()
        with self.assertRaises(ValidationError):
            self.budget.line_ids.unlink()

    def test_two_overlapping_approved_budgets_both_count(self):
        """Deliberate, and asserted so it is a decision rather than an accident.

        A company-wide budget and a departmental one may both touch the same
        account for the same period. They are two real plans, so the figure is
        their sum — but nothing said so until this test.
        """
        self.budget.action_approve()
        second = self.Budget.create({
            'name': 'March top-up',
            'date_from': date(2026, 3, 1), 'date_to': date(2026, 3, 31),
            'line_ids': [(0, 0, {'account_id': self.expense.id,
                                 'amount': 500.0})]})
        second.action_approve()
        budgeted = self.Budget._nc_budgeted(date(2026, 3, 1), date(2026, 3, 31))
        # 12,000 * 31/365 (pro-rated) + 500 (fully inside the window)
        self.assertAlmostEqual(budgeted[self.expense.id],
                               12000.0 * 31 / 365 + 500.0, places=2)

    def test_a_budget_cannot_end_before_it_starts(self):
        with self.assertRaises(ValidationError):
            self.Budget.create({
                'name': 'Backwards', 'date_from': date(2026, 12, 31),
                'date_to': date(2026, 1, 1)})

    # ---- the pro-rata rule (this ticket's judgement call) ----------------

    def test_overlap_is_counted_in_days_inclusive_of_both_ends(self):
        """A single shared day is a full day, not zero. An off-by-one here
        silently halves a month."""
        one_day = self.Budget._nc_overlap_ratio(
            date(2026, 3, 10), date(2026, 3, 10),
            date(2026, 3, 10), date(2026, 3, 10))
        self.assertAlmostEqual(one_day, 1.0, places=6)

    def test_a_year_budget_pro_rates_into_a_month(self):
        """2026 is 365 days; March is 31. 12,000 * 31/365 = 1,019.18."""
        ratio = self.Budget._nc_overlap_ratio(
            date(2026, 1, 1), date(2026, 12, 31),
            date(2026, 3, 1), date(2026, 3, 31))
        self.assertAlmostEqual(ratio, 31 / 365, places=6)
        self.budget.action_approve()
        budgeted = self.Budget._nc_budgeted(date(2026, 3, 1), date(2026, 3, 31))
        self.assertAlmostEqual(budgeted[self.expense.id], 12000.0 * 31 / 365,
                               places=2)

    def test_no_overlap_contributes_nothing(self):
        self.assertAlmostEqual(
            self.Budget._nc_overlap_ratio(
                date(2026, 1, 1), date(2026, 1, 31),
                date(2026, 3, 1), date(2026, 3, 31)), 0.0, places=6)

    # ---- only approved budgets count ------------------------------------

    def test_a_draft_budget_is_not_reported(self):
        """A draft is a proposal. Reporting it would compare actuals against a
        plan nobody agreed to."""
        self.assertFalse(
            self.Budget._nc_budgeted(date(2026, 3, 1), date(2026, 3, 31)))

    def test_a_revised_budget_is_not_reported(self):
        """Superseded, so its figures are history — and counting both it and
        its revision would double the plan."""
        self.budget.action_approve()
        self.budget.action_revise()          # original -> revised, copy -> draft
        self.assertFalse(
            self.Budget._nc_budgeted(date(2026, 3, 1), date(2026, 3, 31)))

    # ---- Budget vs Actual ------------------------------------------------

    def test_variance_is_actual_minus_budget(self):
        """March: actual 1,500, budget 12,000*31/365 = 1,019.18.
        Variance = +480.82 — POSITIVE for an overspend, which is what a budget
        holder means by "over"."""
        self.budget.action_approve()
        budget_total, actual_total, variance = self._report()._nc_totals()
        self.assertAlmostEqual(budget_total, 12000.0 * 31 / 365, places=2)
        self.assertAlmostEqual(actual_total, 1500.0, places=2)
        self.assertAlmostEqual(variance, 1500.0 - 12000.0 * 31 / 365, places=2)
        self.assertGreater(variance, 0.0, "an overspend must read positive")

    def test_the_report_rows_carry_budget_actual_and_variance(self):
        self.budget.action_approve()
        rows = self._report()._nc_compute_lines()
        total = next(r for r in rows if r['label'] == 'TOTAL')
        self.assertAlmostEqual(total['previous_amount'], 12000.0 * 31 / 365,
                               places=2)     # Budget
        self.assertAlmostEqual(total['current_amount'], 1500.0, places=2)
        self.assertAlmostEqual(total['variance'],
                               1500.0 - 12000.0 * 31 / 365, places=2)

    def test_an_unbudgeted_period_reports_nothing_rather_than_zero(self):
        """"No budget covers this" and "on budget" are opposite statements."""
        self.budget.action_approve()
        rows = self._report(date_from=date(2025, 1, 1),
                            date_to=date(2025, 1, 31))._nc_compute_lines()
        self.assertEqual(rows, [])

    def test_draft_entries_do_not_count_as_actual(self):
        """A draft invoice is an intention; a budget that counted it would
        report spending that has not happened."""
        self.budget.action_approve()
        before = self._report()._nc_totals()[1]
        self.env['account.move'].create({
            'move_type': 'entry', 'journal_id': self.journal.id,
            'date': date(2026, 3, 20),
            'line_ids': [
                (0, 0, {'account_id': self.expense.id, 'debit': 9999.0,
                        'credit': 0.0}),
                (0, 0, {'account_id': self.payable.id, 'debit': 0.0,
                        'credit': 9999.0}),
            ]})  # deliberately NOT posted
        self.assertAlmostEqual(before, self._report()._nc_totals()[1], places=2)

    # ---- the contract #120 calls ----------------------------------------

    def test_the_variance_payload_answers_what_analytics_asks(self):
        """`ncollection_account_analytics._nc_budget_variance` calls this when
        the module is present. #120 asserts the ABSENT case; this is the other
        half of the same contract."""
        self.budget.action_approve()
        payload = self.Budget._nc_variance_payload(
            date(2026, 3, 1), date(2026, 3, 31))
        self.assertTrue(payload['available'])
        self.assertAlmostEqual(payload['budget'], 12000.0 * 31 / 365, places=2)
        self.assertAlmostEqual(payload['actual'], 1500.0, places=2)
        self.assertAlmostEqual(payload['variance'],
                               1500.0 - 12000.0 * 31 / 365, places=2)
        self.assertGreater(payload['variance_pct'], 0.0)

    def test_the_payload_reports_unavailable_when_nothing_is_budgeted(self):
        """An unbudgeted period is not a period on budget — the same
        distinction #120 draws when this module is absent entirely."""
        payload = self.Budget._nc_variance_payload(
            date(2025, 1, 1), date(2025, 1, 31))
        self.assertFalse(payload['available'])
        self.assertIn('No approved budget', payload['reason'])
        self.assertNotIn('variance', payload)

    def test_variance_pct_uses_an_absolute_denominator(self):
        """A cost budgeted at -100 and spent at -150 is a 50% OVERSPEND; over a
        signed denominator it would read +50%, i.e. favourable."""
        negative = self.Budget.create({
            'name': 'Credit budget', 'date_from': date(2026, 3, 1),
            'date_to': date(2026, 3, 31),
            'line_ids': [(0, 0, {'account_id': self.expense.id,
                                 'amount': -100.0})]})
        negative.action_approve()
        self.spend(date(2026, 3, 5), 50.0)
        payload = self.Budget._nc_variance_payload(
            date(2026, 3, 1), date(2026, 3, 31))
        self.assertIsNotNone(payload['variance_pct'])

    # ---- plumbing --------------------------------------------------------

    def test_pdf_and_xlsx_render(self):
        import base64
        self.budget.action_approve()
        wizard = self._report()
        self.assertEqual(wizard.action_export_pdf()['type'], 'ir.actions.report')
        self.assertTrue(
            base64.b64decode(wizard._nc_build_xlsx()).startswith(b'PK'))

    def test_the_report_run_is_private_to_its_creator(self):
        """Rule 4 / #413: the wizard is a per-user run. The BUDGETS themselves
        are shared tenant data and deliberately are not scoped this way."""
        other = self.env['res.users'].create({
            'name': 'Other Accountant', 'login': 'f4t02_other',
            'group_ids': [(6, 0, [self.env.ref('account.group_account_readonly').id,
                                  self.env.ref('base.group_user').id])]})
        self.budget.action_approve()
        mine = self._report()
        self.assertFalse(
            self.Report.with_user(other).search([('id', '=', mine.id)]),
            "another user could read someone else's Budget vs Actual run")
        self.assertTrue(
            self.Budget.with_user(other).search([('id', '=', self.budget.id)]),
            "a budget is shared tenant data — scoping it to its creator would "
            "hide the company's own plan from everyone but whoever typed it")

    def test_a_user_without_the_accounting_group_is_denied(self):
        user = self.env['res.users'].create({
            'name': 'No Accounting', 'login': 'f4t02_noacc',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        with self.assertRaises(AccessError):
            self.Budget.with_user(user).create({
                'name': 'X', 'date_from': date(2026, 1, 1),
                'date_to': date(2026, 12, 31)})
