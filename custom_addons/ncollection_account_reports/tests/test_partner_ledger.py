# -*- coding: utf-8 -*-
"""F2-T05: Partner Ledger — scoping, per-partner opening, running balance.

THE ASSERTION THAT MATTERS is the closing balance: for a customer, the ledger's
last running balance must equal that partner's real outstanding receivable. A
report that renders beautifully and is financially wrong is worse than one that
crashes, because nobody notices — so the numbers here are computed by hand in
the fixture and compared exactly, never derived from the code under test.
"""
from datetime import date

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestPartnerLedger(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.PL = cls.env['ncollection.account.report.partner.ledger']
        cls.receivable = cls.company_data['default_account_receivable']
        cls.payable = cls.company_data['default_account_payable']
        cls.revenue = cls.company_data['default_account_revenue']
        cls.journal = cls.company_data['default_journal_misc']

        def entry(day, account, amount, partner):
            """Dr `account` / Cr revenue, carrying a partner on BOTH legs.

            Both legs carry the partner deliberately: it is what lets the
            account-type scoping below be a real assertion rather than an
            accident of which leg happened to be tagged.
            """
            move = cls.env['account.move'].create({
                'move_type': 'entry', 'journal_id': cls.journal.id, 'date': day,
                'line_ids': [
                    (0, 0, {'account_id': account.id, 'debit': amount,
                            'credit': 0.0, 'partner_id': partner.id}),
                    (0, 0, {'account_id': cls.revenue.id, 'debit': 0.0,
                            'credit': amount, 'partner_id': partner.id}),
                ]})
            move.action_post()
            return move

        # partner_a — a customer, with history BEFORE the window and inside it.
        entry(date(2025, 11, 30), cls.receivable, 300.0, cls.partner_a)   # opening
        entry(date(2026, 3, 10), cls.receivable, 1000.0, cls.partner_a)
        entry(date(2026, 6, 20), cls.receivable, 250.0, cls.partner_a)
        # partner_b — a vendor, payable side only.
        entry(date(2026, 4, 1), cls.payable, 500.0, cls.partner_b)

        cls.date_from = date(2026, 1, 1)
        cls.date_to = date(2026, 12, 31)

    def _run(self, scope='receivable', **kw):
        vals = {'date_from': self.date_from, 'date_to': self.date_to,
                'target_move': 'posted', 'partner_scope': scope}
        vals.update(kw)
        return self.PL.create(vals)

    def _rows(self, wizard):
        return wizard._nc_partner_rows()

    # ---- scoping -------------------------------------------------------

    def test_receivable_scope_excludes_the_revenue_leg(self):
        """The revenue leg carries the SAME partner, so a report that grouped
        every account by partner would double-count it. This is the assertion
        that makes the account-type restriction real."""
        rows = self._rows(self._run('receivable'))
        accounts = {r['account_id'] for r in rows if not r['is_initial']}
        self.assertNotIn(self.revenue.id, accounts,
                         "the revenue leg leaked into a receivable ledger — the "
                         "account_type scoping is not applied")
        self.assertEqual(accounts, {self.receivable.id})

    def test_vendor_scope_shows_only_the_payable_partner(self):
        rows = self._rows(self._run('payable'))
        partners = {r['partner_id'] for r in rows}
        self.assertIn(self.partner_b.id, partners)
        self.assertNotIn(self.partner_a.id, partners,
                         "a receivable-only partner appeared in a vendor ledger")

    def test_both_scope_covers_customers_and_vendors(self):
        partners = {r['partner_id'] for r in self._rows(self._run('both'))}
        self.assertIn(self.partner_a.id, partners)
        self.assertIn(self.partner_b.id, partners)

    # ---- the numbers ---------------------------------------------------

    def test_opening_is_per_partner_and_excludes_the_period(self):
        """300 posted before date_from; the 1000 and 250 inside it must not
        appear in the opening row."""
        rows = self._rows(self._run('receivable'))
        opening = [r for r in rows
                   if r['is_initial'] and r['partner_id'] == self.partner_a.id]
        self.assertEqual(len(opening), 1)
        self.assertAlmostEqual(opening[0]['running_balance'], 300.0, places=2)

    def test_closing_balance_equals_the_partners_outstanding_receivable(self):
        """THE acceptance number: 300 opening + 1000 + 250 = 1550.

        Computed by hand from the fixture, not from the code under test — a
        report is only useful if this figure is right.
        """
        rows = [r for r in self._rows(self._run('receivable'))
                if r['partner_id'] == self.partner_a.id]
        self.assertAlmostEqual(rows[-1]['running_balance'], 1550.0, places=2)

    def test_the_running_balance_actually_runs(self):
        """Each row's balance is the previous plus that row's movement.

        Asserting only the final figure would pass against a report that showed
        the closing balance on every line.
        """
        rows = [r for r in self._rows(self._run('receivable'))
                if r['partner_id'] == self.partner_a.id]
        running = rows[0]['running_balance']
        for row in rows[1:]:
            running += row['debit'] - row['credit']
            self.assertAlmostEqual(row['running_balance'], running, places=2)
        self.assertGreater(len(rows), 2, "fixture produced too few rows to test "
                                         "a running balance")

    def test_a_partner_filter_narrows_the_report(self):
        rows = self._rows(self._run('both', partner_ids=[(6, 0, [self.partner_b.id])]))
        self.assertEqual({r['partner_id'] for r in rows}, {self.partner_b.id})

    # ---- plumbing inherited from the engine ----------------------------

    def test_compute_lines_matches_the_on_screen_rows(self):
        """The PDF/XLSX channel and the list must show the same thing — they
        diverging silently is how an export starts lying."""
        wizard = self._run('receivable')
        self.assertEqual(wizard._nc_compute_lines(), wizard._nc_partner_rows())

    def test_action_view_materialises_rows_and_pins_its_view(self):
        action = self._run('receivable').action_view()
        self.assertEqual(action['res_model'],
                         'ncollection.account.report.partner.line')
        self.assertTrue(action['views'], "the list view is not pinned — Odoo may "
                                         "pick a different one")
        created = self.env['ncollection.account.report.partner.line'].search(
            action['domain'][0][2] and [('id', 'in', action['domain'][0][2])])
        self.assertTrue(created)

    def test_drill_down_is_scoped_to_the_reported_account_types(self):
        """The wizard domain is partner-scoped but not type-scoped, so the
        drill-down adds that itself. Without it a customer drill-down would
        show the revenue legs the report deliberately excluded."""
        self._run('receivable').action_view()
        line = self.env['ncollection.account.report.partner.line'].search(
            [('is_initial', '=', False)], limit=1)
        domain = line.action_drill_down()['domain']
        self.assertIn(('account_id.account_type', 'in', ['asset_receivable']),
                      domain)

    def test_a_user_without_the_accounting_group_is_denied(self):
        """Mirrors every sibling report: the ACL gates on
        account.group_account_readonly."""
        user = self.env['res.users'].create({
            'name': 'No Accounting', 'login': 'f2t05_noacc',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        with self.assertRaises(AccessError):
            self.PL.with_user(user).create({
                'date_from': self.date_from, 'date_to': self.date_to,
                'target_move': 'posted'})
