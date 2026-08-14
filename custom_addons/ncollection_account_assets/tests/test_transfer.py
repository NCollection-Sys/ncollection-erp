# -*- coding: utf-8 -*-
"""F4-T03: the transfer flow — "disposal + transfer with correct accounting
impact".

THE INVARIANT, and the only thing worth reviewing here:

    a transfer changes WHERE value sits, never HOW MUCH.

Net book value unchanged, P&L untouched, depreciation schedule untouched. Each
gets its own assertion, because each fails independently and two of the three
fail silently.
"""
from datetime import date

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import AssetCommon


@tagged('post_install', '-at_install')
class TestAssetTransfer(AssetCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # 5,000 over 5 years from 2026-01-01, with the first year posted:
        # accumulated 1,000, net book value 4,000.
        cls.asset = cls._asset()
        cls.posted = cls._post_board_through(cls.asset, date(2026, 12, 31))
        cls.plan_a = cls.env['account.analytic.plan'].create({'name': 'Dept'})
        cls.dept_sales = cls.env['account.analytic.account'].create({
            'name': 'Sales', 'plan_id': cls.plan_a.id})
        cls.dept_hr = cls.env['account.analytic.account'].create({
            'name': 'HR', 'plan_id': cls.plan_a.id})

    def test_the_fixture_actually_posted_something(self):
        """Guards every figure below. A helper that silently posted nothing
        would make each expected number look like a code bug — and a suite that
        measures nothing reports green (#385)."""
        self.assertGreaterEqual(self.posted, 1)
        self.assertAlmostEqual(self.asset.value_depreciated, 1000.0, 2)

    # ---- analytic-only transfer ------------------------------------------

    def test_an_analytic_only_transfer_posts_NO_journal_entry(self):
        """A department change is not a general-ledger event. Posting a
        balanced zero entry for it would fill the journal with noise that hides
        the reclassifications that DID move money."""
        before = self.env['account.move'].search_count([])
        wizard = self.Transfer.create({
            'asset_id': self.asset.id, 'date': date(2027, 1, 1),
            'analytic_distribution': {str(self.dept_hr.id): 100.0},
        })
        wizard.action_transfer()
        self.assertEqual(self.env['account.move'].search_count([]), before,
                         "an analytic-only transfer created a journal entry")
        self.assertEqual(self.asset.analytic_distribution,
                         {str(self.dept_hr.id): 100.0})

    def test_future_depreciation_follows_the_new_distribution(self):
        """The point of an analytic transfer: cost lands on the new centre."""
        self.Transfer.create({
            'asset_id': self.asset.id, 'date': date(2027, 1, 1),
            'analytic_distribution': {str(self.dept_hr.id): 100.0},
        }).action_transfer()
        self._post_board_through(self.asset, date(2027, 12, 31))
        line = self.asset.depreciation_line_ids.filtered(
            lambda line: line.line_date and line.line_date.year == 2027
            and line.move_id)
        self.assertTrue(line, "2027 did not post, so this proves nothing")
        expense = line.move_id.line_ids.filtered(
            lambda aml: aml.account_id == self.depr_expense)
        self.assertEqual(expense.analytic_distribution,
                         {str(self.dept_hr.id): 100.0})

    # ---- reclassification -------------------------------------------------

    def test_a_reclassification_moves_gross_AND_accumulated(self):
        """Four legs, not two. Moving the gross cost alone would leave the
        accumulated depreciation stranded against an account that no longer
        holds the asset — the balance sheet still totals correctly, and both
        fixed-asset lines are wrong."""
        wizard = self.Transfer.create({
            'asset_id': self.asset.id, 'date': date(2027, 1, 1),
            'profile_id': self.profile_veh.id,
        })
        wizard.action_transfer()
        move = self.env['account.move'].search(
            [('ref', 'like', 'Asset transfer')], order='id desc', limit=1)
        self.assertTrue(move, "no reclassification entry was posted")
        self.assertEqual(move.state, 'posted')
        amounts = {line.account_id: (line.debit, line.credit)
                   for line in move.line_ids}
        self.assertEqual(amounts[self.gross_veh], (5000.0, 0.0))
        self.assertEqual(amounts[self.gross_it], (0.0, 5000.0))
        self.assertEqual(amounts[self.accum_it], (1000.0, 0.0))
        self.assertEqual(amounts[self.accum_veh], (0.0, 1000.0))

    def test_a_transfer_does_not_touch_the_P_AND_L(self):
        """THE invariant. A gain or loss belongs to a disposal; a transfer that
        produced one would invent profit out of an internal move."""
        self.Transfer.create({
            'asset_id': self.asset.id, 'date': date(2027, 1, 1),
            'profile_id': self.profile_veh.id,
        }).action_transfer()
        move = self.env['account.move'].search(
            [('ref', 'like', 'Asset transfer')], order='id desc', limit=1)
        pl = move.line_ids.filtered(
            lambda aml: aml.account_id.account_type.startswith(
                ('income', 'expense')))
        self.assertFalse(
            pl, "a transfer hit the P&L on %s — that is a disposal, not a "
                "transfer" % pl.mapped('account_id.code'))

    def test_a_transfer_leaves_net_book_value_unchanged(self):
        """Gross moved, accumulated moved, and the difference between them is
        the same number in the same currency."""
        before = self.asset.purchase_value - self.asset.value_depreciated
        self.Transfer.create({
            'asset_id': self.asset.id, 'date': date(2027, 1, 1),
            'profile_id': self.profile_veh.id,
        }).action_transfer()
        after = self.asset.purchase_value - self.asset.value_depreciated
        self.assertAlmostEqual(before, after, 2)
        self.assertAlmostEqual(after, 4000.0, 2)

    def test_a_transfer_does_not_re_plan_the_depreciation_schedule(self):
        """Moving a laptop from Sales to HR does not restart its useful life.

        Odoo's own `profile_id` onchange copies the method fields, which is
        right for a NEW asset and wrong here — so the wizard writes profile_id
        directly to bypass it. If someone "fixes" that by routing through the
        onchange, the destination profile's terms would silently overwrite the
        asset's, and nobody would notice until a year-end.
        """
        other = self._profile('Fast Vehicles', self.gross_veh, self.accum_veh)
        other.write({'method_number': 2})
        before = (self.asset.method, self.asset.method_number,
                  self.asset.method_period, self.asset.date_start)
        dates = self.asset.depreciation_line_ids.mapped('line_date')
        self.Transfer.create({
            'asset_id': self.asset.id, 'date': date(2027, 1, 1),
            'profile_id': other.id,
        }).action_transfer()
        self.assertEqual(
            (self.asset.method, self.asset.method_number,
             self.asset.method_period, self.asset.date_start), before,
            "the transfer re-planned the asset's depreciation terms")
        self.assertEqual(self.asset.depreciation_line_ids.mapped('line_date'),
                         dates, "the transfer rewrote the depreciation board")

    def test_the_preserved_field_list_is_not_empty(self):
        """The preservation is DISCOVERED at runtime, so it can silently
        discover nothing — a helper returning `[]` would preserve nothing and
        every other test here would still pass, because net book value and the
        reclassification entry are correct either way.

        Two of the eleven change money rather than presentation, so both are
        named explicitly: salvage_value moves the depreciation BASE, and
        analytic_distribution silently re-points future cost.
        """
        wizard = self.Transfer.create(
            {'asset_id': self.asset.id, 'date': date(2027, 1, 1)})
        derived = wizard._nc_profile_derived_fields()
        self.assertIn('method_number', derived)
        self.assertIn('salvage_value', derived)
        self.assertIn('analytic_distribution', derived)
        self.assertGreaterEqual(len(derived), 5)

    def test_a_transfer_does_not_change_the_salvage_value(self):
        """The field that moves MONEY. Salvage value sets the depreciation
        base, so inheriting the destination's would re-scale every remaining
        period — a correct-looking transfer with a quietly different charge."""
        rich = self._profile('Vehicles (salvaged)', self.gross_veh,
                             self.accum_veh)
        rich.write({'salvage_value': 500.0})
        before = self.asset.salvage_value
        self.Transfer.create({
            'asset_id': self.asset.id, 'date': date(2027, 1, 1),
            'profile_id': rich.id,
        }).action_transfer()
        self.assertAlmostEqual(self.asset.salvage_value, before, 2,
                               "the transfer inherited the destination "
                               "profile's salvage value")

    def test_a_profile_change_sharing_both_accounts_posts_nothing(self):
        """A profile can differ only by method. Nothing moved in the GL, so
        there is nothing to post — and a zero-value entry is still noise."""
        twin = self._profile('IT Equipment (36m)', self.gross_it,
                             self.accum_it)
        before = self.env['account.move'].search_count([])
        self.Transfer.create({
            'asset_id': self.asset.id, 'date': date(2027, 1, 1),
            'profile_id': twin.id,
        }).action_transfer()
        self.assertEqual(self.env['account.move'].search_count([]), before)
        self.assertEqual(self.asset.profile_id, twin,
                         "the profile was not actually changed")

    # ---- refusals ---------------------------------------------------------

    def test_only_PLANNED_depreciation_is_excluded_from_the_reclassification(self):
        """The entry must move what the GL actually holds.

        The board runs to 2030 but only 2026 is posted, so accumulated is
        1,000 — not the 5,000 the full plan implies. Reclassifying planned
        amounts would move value the general ledger has never seen and leave
        the two asset accounts permanently out of step with their sub-ledger.
        """
        planned = sum(self.asset.depreciation_line_ids.filtered(
            lambda line: line.type == 'depreciate').mapped('amount'))
        self.assertGreater(planned, 1000.0, "the board has no future lines, "
                                            "so this test proves nothing")
        wizard = self.Transfer.create({
            'asset_id': self.asset.id, 'date': date(2027, 1, 1),
            'profile_id': self.profile_veh.id,
        })
        self.assertAlmostEqual(wizard._nc_accumulated(), 1000.0, 2)

    def test_a_BARE_profile_change_is_still_refused(self):
        """The half of the OCA guard this module must NOT have removed.

        `account_asset_management._check_profile_change` refuses any profile
        change on an asset with posted entries, because a bare edit re-points
        future depreciation while every past entry stays against the old
        accounts — the sub-ledger and the GL diverge permanently and nothing
        reports it. The transfer wizard is allowed past that guard only because
        it posts the entry that moves the GL to match.

        If someone ever "simplifies" models/asset.py into an unconditional
        bypass, every other test here keeps passing and this one fails. That is
        the entire point of it.
        """
        with self.assertRaises(UserError):
            self.asset.write({'profile_id': self.profile_veh.id})

    # The forgeability of the exemption — and the reason it is no longer a
    # context key at all — is covered in test_review_round.py, which asserts
    # the RPC-shaped call directly rather than only the accidental-leak case
    # this file used to test.

    def test_a_transfer_with_no_destination_is_refused(self):
        wizard = self.Transfer.create(
            {'asset_id': self.asset.id, 'date': date(2027, 1, 1)})
        with self.assertRaises(UserError):
            wizard.action_transfer()

    def test_a_removed_asset_cannot_be_transferred(self):
        """It has already left the balance sheet; moving it between accounts
        would resurrect a value that is no longer there."""
        self.asset.write({'state': 'removed'})
        wizard = self.Transfer.create({
            'asset_id': self.asset.id, 'date': date(2027, 1, 1),
            'profile_id': self.profile_veh.id,
        })
        with self.assertRaises(UserError):
            wizard.action_transfer()
