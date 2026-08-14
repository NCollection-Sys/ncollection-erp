# -*- coding: utf-8 -*-
"""F4-T03: the account-type guard.

THE FAILURE THIS PREVENTS PRODUCES NO SYMPTOM. A profile pointed at a plain
`expense` account still posts, still balances, and still reconciles — #114's
cash flow adds depreciation back in Operating and removes it from Investing,
equal and opposite, so the TOTAL is right no matter how the accounts are typed.
Only the Operating/Investing split, the P&L's Operating Expenses line and the
Gross Margin KPI are wrong.

So these tests do two separate jobs, and both are needed:

  1. assert the guard refuses a bad profile (the ordinary half), and
  2. assert the guard's classification still AGREES with #114's, which is the
     half that decays. #114 keys on `expense_depreciation`; if either side ever
     edits its tuple alone, the guard would keep passing while protecting
     nothing — the precise shape of the three guards this repo has shipped that
     scanned clean while broken (#330, #348, #311).
"""
from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.ncollection_account_reports.wizard import cash_flow

from .common import AssetCommon


@tagged('post_install', '-at_install')
class TestAccountTypeGuard(AssetCommon):

    # ---- the guard refuses ------------------------------------------------

    def test_a_correctly_typed_profile_is_accepted(self):
        """The fixture itself. If this fails, every other assertion here is
        measuring the fixture rather than the guard."""
        self.assertTrue(self.profile_it.id)
        self.assertEqual(
            self.profile_it.account_expense_depreciation_id.account_type,
            'expense_depreciation')

    def test_a_plain_expense_account_is_refused_for_the_expense_leg(self):
        """THE case. `expense` and `expense_depreciation` are both perfectly
        valid expense accounts and a finance admin picking the first would see
        nothing wrong — the cash flow keeps balancing."""
        plain = self._account('NCA6100', 'Office Costs', 'expense')
        with self.assertRaises(ValidationError) as caught:
            self._profile('Bad', self.gross_it, self.accum_it).write({
                'account_expense_depreciation_id': plain.id})
        self.assertIn('expense_depreciation', str(caught.exception))

    def test_a_current_asset_is_refused_for_accumulated_depreciation(self):
        """cash_flow.py's docstring names this one explicitly: offsetting
        depreciation against a current asset shifts the amount out of Operating
        twice and the split is wrong."""
        current = self._account('NCA1200', 'Prepaid', 'asset_current')
        with self.assertRaises(ValidationError):
            self._profile('Bad', self.gross_it, current)

    def test_a_current_asset_is_refused_for_the_gross_asset_account(self):
        current = self._account('NCA1300', 'Stock', 'asset_current')
        with self.assertRaises(ValidationError):
            self._profile('Bad', current, self.accum_it)

    def test_the_guard_fires_on_WRITE_and_not_only_on_CREATE(self):
        """@api.constrains runs only when one of its DECLARED fields is in the
        written values (`_validate_fields`:
        `if not field_names.isdisjoint(check._constrains)`). A constraint
        naming fewer fields than it reads fires on create and then never again.

        That exact defect shipped in #121 — an acceptance criterion claiming
        immutability while an RPC write sailed through — and the test that
        "covered" it could not tell the working guard from the inert one. One
        write per guarded field, so narrowing the decorator to any subset fails
        here.
        """
        plain = self._account('NCA6200', 'Other', 'expense')
        current = self._account('NCA1400', 'Debtors', 'asset_receivable')
        for field, bad in (
                ('account_expense_depreciation_id', plain),
                ('account_depreciation_id', current),
                ('account_asset_id', current)):
            profile = self._profile(
                'Rewritable %s' % field, self.gross_veh, self.accum_veh)
            with self.assertRaises(ValidationError,
                                   msg="%s is not guarded on write" % field):
                profile.write({field: bad.id})

    # ---- the guard still agrees with what it protects ---------------------

    def test_the_expense_type_still_matches_the_cash_flow_add_back(self):
        """#114 adds back exactly one account type as non-cash. If it ever
        starts keying on a different one, this guard protects nothing."""
        guarded = self.Profile._nc_guarded_account_types()
        self.assertEqual(
            tuple(guarded['account_expense_depreciation_id']),
            ('expense_depreciation',),
            "the guard no longer enforces the type the cash flow adds back")
        self.assertIn('expense_depreciation', cash_flow.PROFIT_AND_LOSS)
        self.assertIn('expense_depreciation', cash_flow.OPERATING)

    def test_the_fixed_asset_types_still_match_the_investing_section(self):
        """cash_flow.py's INVESTING tuple IS the set this guard allows for both
        balance-sheet legs. Compared as sets, not by substring: a substring
        check would be satisfied by a longer, different tuple."""
        guarded = self.Profile._nc_guarded_account_types()
        self.assertEqual(set(guarded['account_asset_id']),
                         set(cash_flow.INVESTING))
        self.assertEqual(set(guarded['account_depreciation_id']),
                         set(cash_flow.INVESTING))

    def test_every_guarded_field_is_one_the_posting_path_actually_reads(self):
        """A guard on a field nobody posts through is decoration.

        account_asset_line.create_move() reads the depreciation and expense
        accounts off the profile; the asset account is what the gross value
        sits in. All three must exist on the model — a rename upstream would
        otherwise leave the constraint silently guarding nothing.
        """
        for fname in self.Profile._nc_guarded_account_types():
            self.assertIn(fname, self.Profile._fields,
                          "%s no longer exists on account.asset.profile — the "
                          "guard is checking a field that is gone" % fname)
