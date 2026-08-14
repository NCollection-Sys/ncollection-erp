# -*- coding: utf-8 -*-
"""F4-T03: the account-type guard — the one thing OCA cannot enforce for us.

WHY THIS FILE EXISTS AT ALL.

``account_asset_management`` posts every depreciation period as a real
``account.move``: debit ``account_expense_depreciation_id``, credit
``account_depreciation_id`` (accumulated). Both are plain, unconstrained
``Many2one('account.account')`` fields on the profile. Any account is accepted.

Two reports already shipped in this repo depend on those accounts being typed a
particular way, and BOTH fail silently rather than loudly if they are not:

* **#114 cash flow (indirect method).** ``expense_depreciation`` is added back
  in Operating as a non-cash charge and removed from Investing — equal and
  opposite, so *the statement still balances and still reconciles to the cash
  movement* whatever the accounts are typed. What breaks is the
  Operating/Investing SPLIT. `cash_flow.py`'s own module docstring states the
  assumption in prose:

      "What it assumes is that the credit side (accumulated depreciation) is
      typed ``asset_fixed``/``asset_non_current``, which is where a conventional
      chart puts it."

  This module turns that prose assumption into an enforced constraint. Prose
  that nothing checks is how #422's exemption reason came to be false, and how
  three guards in this repo came to scan clean while broken (#330, #348, #311).

* **#411's Balance Sheet / P&L maps.** ``expense_depreciation`` sits inside
  Operating Expenses and Accumulated Earnings; ``asset_fixed`` sits inside
  Non-Current Assets. A depreciation charge landing on ``expense_direct_cost``
  would move it into Cost of Sales and change reported Gross Margin — which
  ``ncollection_account_analytics`` then publishes as a FINANCIAL KPI (#120).

The failure mode is the dangerous kind: nothing errors, nothing fails to
balance, and every total is right. Only the ATTRIBUTION is wrong. A tenant
would have to reconcile the cash-flow split by hand to notice.

WHY A CONSTRAINT AND NOT A REPORT-SIDE FIX. The reports cannot detect it. By
the time a journal item exists, the only evidence of intent is the account type
itself — the very thing that is wrong. The choke point is configuration time,
which is here.

SCOPE OF THE GUARD. It runs on the PROFILE, which is the single place the
posting path reads from (``account_asset_line.create_move`` reads
``asset.profile_id.account_depreciation_id`` and
``...account_expense_depreciation_id``; ``account.asset`` itself carries no
account fields). One check, no gaps.
"""
from odoo import api, models
from odoo.exceptions import ValidationError

# The depreciation EXPENSE leg. #114 keys its non-cash add-back on exactly this
# account type, and #411 places it in Operating Expenses.
DEPRECIATION_EXPENSE_TYPES = ('expense_depreciation',)

# The fixed-asset side: the gross asset account and its accumulated-depreciation
# contra. #114's INVESTING tuple is exactly ('asset_fixed', 'asset_non_current'),
# so these must match it or the section split moves.
FIXED_ASSET_TYPES = ('asset_fixed', 'asset_non_current')

# field -> (allowed types, what breaks). The message names the CONSEQUENCE,
# because "wrong account type" alone does not tell a finance admin why they
# should care about a setting that appears to work.
_GUARDED_ACCOUNTS = {
    'account_expense_depreciation_id': (
        DEPRECIATION_EXPENSE_TYPES,
        "the depreciation expense leg. The Cash Flow report (indirect method) "
        "adds this back as a non-cash charge and removes it from Investing, "
        "keyed on the 'expense_depreciation' type. Typed anything else, the "
        "statement still balances and still reconciles — but the "
        "Operating/Investing split, the P&L's Operating Expenses line and the "
        "Gross Margin KPI are all wrong, with nothing to indicate it."),
    'account_depreciation_id': (
        FIXED_ASSET_TYPES,
        "the accumulated-depreciation contra account. The Cash Flow report "
        "assumes this offsets an Investing account; typed as a current asset "
        "the amount is shifted out of Operating twice and the split is wrong "
        "(the total, and therefore the reconciliation, is not)."),
    'account_asset_id': (
        FIXED_ASSET_TYPES,
        "the gross fixed-asset account. Typed outside Non-Current Assets, "
        "acquisitions and disposals stop appearing as Investing activity and "
        "the Balance Sheet reports the asset in the wrong section."),
}


class AccountAssetProfile(models.Model):
    _inherit = 'account.asset.profile'

    # EVERY guarded field is declared. Odoo runs a constraint only when one of
    # its declared fields appears in the written values (`_validate_fields`:
    # `if not field_names.isdisjoint(check._constrains)`), so a constraint
    # naming fewer fields than it reads fires on create and then never again on
    # a targeted write. That exact defect shipped in #121 — a guard whose
    # acceptance criterion claimed immutability while an RPC write sailed
    # through — and the test that "covered" it could not tell the working guard
    # from the inert one.
    @api.constrains('account_expense_depreciation_id',
                    'account_depreciation_id',
                    'account_asset_id')
    def _nc_check_account_types(self):
        """Refuse a profile whose posting accounts would misreport.

        Deliberately a ValidationError, not a warning: a warning on a
        configuration screen is dismissed once and then wrong forever, and the
        thing it is protecting produces no visible symptom.
        """
        for profile in self:
            for fname, (allowed, why) in _GUARDED_ACCOUNTS.items():
                account = profile[fname]
                if not account:
                    # All three are `required=True` upstream, so this branch is
                    # only reachable mid-construction. Let the required-field
                    # error speak rather than emitting a second, vaguer one.
                    continue
                if account.account_type in allowed:
                    continue
                raise ValidationError(self.env._(
                    "Asset profile %(profile)s: %(label)s is set to "
                    "%(account)s, which is typed '%(actual)s'. It must be one "
                    "of: %(allowed)s.\n\n"
                    "This account is %(why)s",
                    profile=profile.display_name,
                    label=self._fields[fname].string,
                    account=account.display_name,
                    actual=account.account_type,
                    allowed=", ".join(allowed),
                    why=why,
                ))

    @api.model
    def _nc_guarded_account_types(self):
        """The guard's rules, as data.

        Exposed so tests — and #114's own suite, should it ever want to assert
        the contract from the other end — can read the classification instead of
        restating it. A second copy of these tuples is a second thing to drift.
        """
        return {fname: allowed for fname, (allowed, _why) in
                _GUARDED_ACCOUNTS.items()}
