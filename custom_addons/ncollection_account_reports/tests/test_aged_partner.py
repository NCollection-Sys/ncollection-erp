# -*- coding: utf-8 -*-
"""F2-T05: Aged Receivable / Payable — bucketing and the reconciliation.

THE ACCEPTANCE CRITERION FPA STATES is "outstanding balances match customer
ledger". That is asserted here as a real cross-report check: the aged report's
per-partner total must equal the Partner Ledger's closing balance for the same
partner and date. The two reports compute it differently on purpose — the
ledger sums every posted line's BALANCE, the aged report sums open RESIDUALS —
so agreement is evidence, not a tautology.

Buckets follow FINANCIAL_PLATFORM_ARCHITECTURE.md: Current · 1-30 · 31-60 ·
61-90 · 91-120 · Over 120, produced by period_length=30.
"""
from datetime import date, timedelta

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import UserError
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestAgedPartnerBalance(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Aged = cls.env['ncollection.account.report.aged.partner']
        cls.PL = cls.env['ncollection.account.report.partner.ledger']
        cls.as_at = date(2026, 6, 30)

        # One open invoice per bucket, due N days before the as-at date, so a
        # mis-bucketed line lands in a neighbour and the test names which.
        # 0 -> current (due in the future), then 10/45/75/100/200 days overdue.
        cls.offsets = {'bucket_current': -5, 'bucket_1': 10, 'bucket_2': 45,
                       'bucket_3': 75, 'bucket_4': 100, 'bucket_over': 200}
        cls.amounts = {'bucket_current': 100.0, 'bucket_1': 200.0,
                       'bucket_2': 300.0, 'bucket_3': 400.0,
                       'bucket_4': 500.0, 'bucket_over': 600.0}
        for key, days in cls.offsets.items():
            cls._invoice(cls.partner_a, cls.amounts[key],
                         cls.as_at - timedelta(days=days))

    @classmethod
    def _invoice(cls, partner, amount, due):
        """A posted customer invoice with an explicit due date."""
        move = cls.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': partner.id,
            'invoice_date': min(due, cls.as_at),
            'invoice_date_due': due,
            'invoice_line_ids': [(0, 0, {
                'name': 'aging probe', 'quantity': 1, 'price_unit': amount,
                'tax_ids': [],
            })],
        })
        move.action_post()
        return move

    def _aged(self, **kw):
        vals = {'date_from': date(2026, 1, 1), 'date_to': self.as_at,
                'target_move': 'posted', 'partner_scope': 'receivable'}
        vals.update(kw)
        return self.Aged.create(vals)

    def _row(self, wizard, partner=None):
        partner = partner or self.partner_a
        rows = [r for r in wizard._nc_aged_rows() if r['partner_id'] == partner.id]
        self.assertEqual(len(rows), 1, "expected exactly one row for the partner")
        return rows[0]

    # ---- bucketing -----------------------------------------------------

    def test_each_invoice_lands_in_its_own_bucket(self):
        """Six invoices, six buckets, one each — the whole point of the report.

        Asserting only the total would pass against a report that dumped every
        invoice into a single column.
        """
        row = self._row(self._aged())
        for key, amount in self.amounts.items():
            with self.subTest(bucket=key):
                self.assertAlmostEqual(
                    row[key], amount, places=2,
                    msg="%s holds %.2f, expected %.2f — a line is in the wrong "
                        "bucket" % (key, row[key], amount))

    def test_a_line_due_today_is_current_not_overdue(self):
        """The boundary that is easiest to get wrong by one day."""
        self._invoice(self.partner_b, 50.0, self.as_at)
        row = self._row(self._aged(), self.partner_b)
        self.assertAlmostEqual(row['bucket_current'], 50.0, places=2)
        self.assertAlmostEqual(row['bucket_1'], 0.0, places=2)

    def test_period_length_re_cuts_the_buckets(self):
        """Configurability, asserted rather than assumed: at 60-day steps the
        45-day-overdue invoice moves from the second bucket into the first."""
        wide = self._row(self._aged(period_length=60))
        self.assertAlmostEqual(wide['bucket_1'], 200.0 + 300.0, places=2)
        self.assertAlmostEqual(wide['bucket_2'], 400.0 + 500.0, places=2)

    def test_bucket_labels_follow_period_length(self):
        """Headings must not claim 1-30 while the arithmetic uses 45."""
        self.assertEqual(self._aged(period_length=45)._nc_bucket_labels()[1], '1-45')

    def test_a_non_positive_period_is_refused(self):
        with self.assertRaises(UserError):
            self._aged(period_length=0)._nc_aged_rows()

    # ---- the FPA acceptance criterion ----------------------------------

    def test_total_matches_the_partner_ledger_closing_balance(self):
        """FPA: "Outstanding balances match customer ledger".

        Computed two different ways — the ledger sums posted BALANCES, the aged
        report sums open RESIDUALS — so this is a genuine cross-check, and it is
        the assertion that would catch a bucketing bug that still totals
        correctly, or a ledger that double-counts.
        """
        aged_total = self._row(self._aged())['total']
        ledger = self.PL.create({
            'date_from': date(2000, 1, 1), 'date_to': self.as_at,
            'target_move': 'posted', 'partner_scope': 'receivable'})
        rows = [r for r in ledger._nc_partner_rows()
                if r['partner_id'] == self.partner_a.id]
        self.assertAlmostEqual(
            aged_total, rows[-1]['running_balance'], places=2,
            msg="the aged total and the partner ledger closing balance "
                "disagree — one of the two reports is wrong")
        self.assertAlmostEqual(aged_total, sum(self.amounts.values()), places=2)

    def test_a_paid_invoice_leaves_the_aged_report(self):
        """Aged shows what is still OWED. A fully-reconciled invoice must drop
        out, or the report overstates the debt forever."""
        before = self._row(self._aged())['total']
        invoice = self._invoice(self.partner_b, 777.0,
                                self.as_at - timedelta(days=10))
        payment = self.env['account.payment'].create({
            'payment_type': 'inbound', 'partner_type': 'customer',
            'partner_id': self.partner_b.id, 'amount': 777.0,
            'date': self.as_at,
        })
        payment.action_post()
        (invoice.line_ids + payment.move_id.line_ids).filtered(
            lambda ln: ln.account_id.account_type == 'asset_receivable'
        ).reconcile()
        rows = [r for r in self._aged()._nc_aged_rows()
                if r['partner_id'] == self.partner_b.id]
        self.assertFalse(rows, "a fully paid invoice still appears in the aged "
                               "report")
        self.assertAlmostEqual(self._row(self._aged())['total'], before, places=2)

    def test_a_partially_paid_invoice_ages_only_its_unpaid_remainder(self):
        """The ONLY case where residual and balance differ — and therefore the
        only test that proves the report sums the right one.

        Written after a mutation exposed the gap: swapping `amount_residual`
        for `balance` in the summation passed the whole suite, because
        fully-reconciled lines are already excluded by the DOMAIN and every
        other fixture line is untouched, where the two are equal. A partial
        payment separates them: 1000 invoiced, 400 paid, 600 still owed.
        """
        invoice = self._invoice(self.partner_b, 1000.0,
                                self.as_at - timedelta(days=10))
        payment = self.env['account.payment'].create({
            'payment_type': 'inbound', 'partner_type': 'customer',
            'partner_id': self.partner_b.id, 'amount': 400.0,
            'date': self.as_at,
        })
        payment.action_post()
        (invoice.line_ids + payment.move_id.line_ids).filtered(
            lambda ln: ln.account_id.account_type == 'asset_receivable'
        ).reconcile()

        row = self._row(self._aged(), self.partner_b)
        self.assertAlmostEqual(
            row['total'], 600.0, places=2,
            msg="the aged report shows %.2f — summing `balance` (1000, the "
                "original debt) instead of `amount_residual` (600, what is "
                "still owed)" % row['total'])

    def test_drill_down_shows_only_open_items_for_that_partner(self):
        self._aged().action_view()
        line = self.env['ncollection.account.report.aged.line'].search(
            [('partner_id', '=', self.partner_a.id)], limit=1)
        domain = line.action_drill_down()['domain']
        self.assertIn(('partner_id', '=', self.partner_a.id), domain)
        self.assertIn(('amount_residual', '!=', 0.0), domain)
