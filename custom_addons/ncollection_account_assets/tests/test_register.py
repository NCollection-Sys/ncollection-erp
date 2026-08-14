# -*- coding: utf-8 -*-
"""F4-T03: the Asset Register on the F2 engine.

Every expected figure is a round number worked out by hand — 5,000 over 5 years
is 1,000 a year — so a wrong result is a wrong result and not a second copy of
the code under test.

TWO IDENTITIES the register must satisfy, asserted on the totals row:

    Closing NBV = Purchase Value - Accumulated Depreciation
    Closing NBV = Opening NBV - Depreciation      (absent additions/disposals)

The second is what makes the register a MOVEMENT report rather than a snapshot,
and it is the one that breaks when the opening balance is computed carelessly.
"""
from datetime import date

from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import AssetCommon


@tagged('post_install', '-at_install')
class TestAssetRegister(AssetCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # 5,000 from 2026-01-01 over 5 years. 2026 and 2027 posted:
        # accumulated 2,000, NBV 3,000.
        cls.asset = cls._asset()
        cls.posted = cls._post_board_through(cls.asset, date(2027, 12, 31))

    def _report(self, **overrides):
        values = {'date_from': date(2027, 1, 1), 'date_to': date(2027, 12, 31),
                  'target_move': 'posted'}
        values.update(overrides)
        return self.Register.create(values)

    def test_the_fixture_actually_posted_two_years(self):
        """Guards every figure below (#385: a suite that measures nothing
        reports green)."""
        self.assertEqual(self.posted, 2)
        self.assertAlmostEqual(self.asset.value_depreciated, 2000.0, 2)

    # ---- the figures ------------------------------------------------------

    def test_the_2027_window_reports_the_movement_by_hand(self):
        gross, opening, period, accumulated, closing = \
            self._report()._nc_totals()
        self.assertAlmostEqual(gross, 5000.0, 2)        # purchase value
        self.assertAlmostEqual(opening, 4000.0, 2)      # 5000 - 1000 (2026)
        self.assertAlmostEqual(period, 1000.0, 2)       # 2027's charge
        self.assertAlmostEqual(accumulated, 2000.0, 2)  # 2026 + 2027
        self.assertAlmostEqual(closing, 3000.0, 2)      # 5000 - 2000

    def test_closing_nbv_equals_gross_minus_accumulated(self):
        gross, _opening, _period, accumulated, closing = \
            self._report()._nc_totals()
        self.assertAlmostEqual(closing, gross - accumulated, 2)

    def test_closing_nbv_equals_opening_minus_the_period_charge(self):
        """What makes this a movement report. Breaks first when the opening
        balance is computed off the wrong side of date_from."""
        _gross, opening, period, _accumulated, closing = \
            self._report()._nc_totals()
        self.assertAlmostEqual(closing, opening - period, 2)

    def test_an_asset_acquired_INSIDE_the_window_has_no_opening_balance(self):
        """It was not on the balance sheet on date_from. Reporting an opening
        value there would invent one and break the movement identity for every
        acquisition — the most common thing a register has to explain."""
        new_asset = self._asset(name='Van', value=3000.0,
                                start=date(2027, 6, 1))
        self._post_board_through(new_asset, date(2027, 12, 31))
        report = self._report()
        rows = {row['label']: row for row in report._nc_compute_lines()}
        van = next(row for label, row in rows.items() if 'Van' in label)
        self.assertAlmostEqual(van['opening_balance'], 0.0, 2)
        self.assertAlmostEqual(van['debit'], 3000.0, 2)

    def test_an_asset_acquired_AFTER_the_window_is_absent(self):
        future = self._asset(name='Forklift', start=date(2029, 1, 1))
        labels = [row['label']
                  for row in self._report()._nc_compute_lines()]
        self.assertFalse([label for label in labels if 'Forklift' in label],
                         "an asset acquired after the window was reported")
        self.assertTrue(future.id)

    def test_a_DRAFT_depreciation_entry_is_excluded_under_target_posted(self):
        """`target_move` must actually reach the sub-ledger. Without this the
        register would silently disagree with every other F2 report run with
        the same filter."""
        line = self.asset.depreciation_line_ids.filtered(
            lambda line: line.line_date and line.line_date.year == 2027
            and line.move_id)
        self.assertTrue(line, "nothing posted in 2027; this proves nothing")
        line.move_id.button_draft()
        posted = self._report(target_move='posted')._nc_totals()
        every = self._report(target_move='all')._nc_totals()
        self.assertAlmostEqual(posted[2], 0.0, 2,
                               "a draft entry counted as posted")
        self.assertAlmostEqual(every[2], 1000.0, 2,
                               "target_move='all' dropped a draft entry")

    def test_an_init_entry_counts_even_though_it_has_no_move(self):
        """Accumulated depreciation migrated in from a previous system carries
        no Odoo move BY DESIGN. A register keyed on move_id would report such
        an asset at full gross with zero accumulated — overstating net book
        value by exactly the amount already written off. This is why the
        register reads the sub-ledger and not account.move.line.
        """
        migrated = self._asset(name='Legacy Press', value=4000.0,
                               start=date(2024, 1, 1))
        opening_line = migrated.depreciation_line_ids.filtered(
            lambda line: line.type == 'depreciate')[:1]
        self.assertTrue(opening_line)
        opening_line.with_context(
            allow_asset_line_update=True).write({'init_entry': True})
        report = self._report(date_from=date(2027, 1, 1),
                              date_to=date(2027, 12, 31))
        rows = [row for row in report._nc_compute_lines()
                if 'Legacy Press' in row['label']]
        self.assertTrue(rows, "the migrated asset is missing from the register")
        self.assertGreater(
            rows[0]['credit'], 0.0,
            "an init_entry line was ignored, so a migrated asset reports zero "
            "accumulated depreciation and an overstated net book value")

    # ---- filters and plumbing --------------------------------------------

    def test_the_profile_filter_scopes_the_report(self):
        self._asset(name='Truck', profile=self.profile_veh, value=9000.0)
        every = self._report()._nc_totals()
        scoped = self._report(
            profile_ids=[(6, 0, self.profile_veh.ids)])._nc_totals()
        self.assertAlmostEqual(scoped[0], 9000.0, 2)
        self.assertGreater(every[0], scoped[0])

    def test_an_empty_report_returns_no_rows_rather_than_a_lone_total(self):
        """A TOTAL row with nothing above it reads as "zero assets exist",
        which is a different claim from "nothing matched your filter"."""
        empty = self._report(date_from=date(2020, 1, 1),
                             date_to=date(2020, 12, 31))
        self.assertEqual(empty._nc_compute_lines(), [])

    def test_the_engine_renders_it_end_to_end(self):
        """action_view materialises report lines through the shared engine —
        the acceptance criterion is "via F2 engine", so exercise the engine and
        not just the arithmetic."""
        report = self._report()
        action = report.action_view()
        self.assertEqual(action['res_model'], 'ncollection.account.report.line')
        lines = self.env['ncollection.account.report.line'].browse(
            action['domain'][0][2])
        self.assertTrue(lines)
        total = lines[-1]
        self.assertAlmostEqual(total.closing_balance, 3000.0, 2)

    def test_the_xlsx_export_produces_a_workbook(self):
        book = self._report()._nc_build_xlsx()
        self.assertTrue(book)

    def test_a_users_report_run_is_invisible_to_another_user(self):
        """#413's rule, applied to this module's wizards: a report run carries
        the filters — and for the transfer wizard, the asset about to move."""
        other = self.env['res.users'].create({
            'name': 'Other Accountant', 'login': 'nc_asset_other',
            # readonly explicitly: the register's ACL row grants
            # account.group_account_readonly, and this test is about the RECORD
            # RULE, not about access. A user who cannot reach the model at all
            # would pass it for the wrong reason.
            'group_ids': [(6, 0, [
                self.env.ref('account.group_account_readonly').id,
                self.env.ref('base.group_user').id])],
        })
        mine = self._report()
        self.assertFalse(
            self.Register.with_user(other).search([('id', '=', mine.id)]),
            "another user can read this user's report run")

    def test_a_readonly_accountant_cannot_run_a_transfer(self):
        """Rule 4: the UI restriction on the transfer action is mirrored at the
        ORM, so an RPC call that never sees the menu is still refused."""
        readonly = self.env['res.users'].create({
            'name': 'Read Only', 'login': 'nc_asset_readonly',
            'group_ids': [(6, 0, [
                self.env.ref('account.group_account_readonly').id,
                self.env.ref('base.group_user').id])],
        })
        with self.assertRaises(AccessError):
            self.Transfer.with_user(readonly).create(
                {'asset_id': self.asset.id, 'date': date(2027, 1, 1)})
