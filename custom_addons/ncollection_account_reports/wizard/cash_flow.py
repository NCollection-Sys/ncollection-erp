# -*- coding: utf-8 -*-
"""F2-T04: Native Cash Flow Statement (indirect method).

FPA §Cash Flow Statement: Operating · Investing · Financing over a period, with
the current figure, the previous period and the difference. Built on the F2-T01
engine + the F2-T03 comparison mixin; reads ``account.move.line`` only — Odoo
owns the numbers (FPA §4/§6).

COLUMN NAMES. FPA §Cash Flow calls the columns *Category · Amount · Previous
Period · Difference*; the shared comparison mixin calls the same four things
*Category · Current · Previous · Variance* (and adds Variance %). They are the
same quantities, so this report uses the mixin rather than relabelling: one
column set across Balance Sheet, P&L and Cash Flow is worth more to a reader
than matching the spec's wording in one report and not its neighbours.

THE ONE JUDGEMENT CALL IN THIS FILE is the account-type → section mapping below.
It is the standard classification, but it is a judgement rather than a lookup,
so it is declared in one place, in full, as data — not scattered through the
arithmetic where nobody could review it.

WHY THE RECONCILIATION IS AN IDENTITY, NOT A HOPE. Double entry means every
balance movement across all accounts sums to zero. So if every NON-cash account
is classified into exactly one section, and each section's cash effect is the
NEGATIVE of its accounts' balance movement, then:

    operating + investing + financing
      = -(movement of every non-cash account)
      = movement of the cash accounts

FPA's acceptance — "cash flow reconciles with bank/cash account movements" — is
therefore structural, and the test asserts it against cash-account movement
computed independently (``_nc_cash_balance_at``) rather than against this sum.
The statement itself ends on those same two cash balances, so a reader can check
it on the face of the report.

Two things the identity depends on, both enforced rather than assumed:

* **Every type is classified** (``_nc_assert_every_type_is_classified``). An
  unclassified type would silently drop out of the sum. A cash flow statement
  that quietly omits a category is worse than one that refuses to render, and
  Odoo adding a type in a future version must fail loudly here.
* **The complete books** (``ncollection.account.report.whole.entity``). A
  journal/account/partner filter would leave the two sides of the identity
  looking at different subsets.

``off_balance`` is excluded by design: Odoo forbids a move from mixing
off-balance and regular accounts, so those entries balance among themselves and
carry no cash effect. Including them would add a zero at best and corrupt the
identity at worst.

KNOWN LIMITATION — reclassification into ``off_balance`` after posting. Odoo's
``_check_off_balance`` constraint is declared on ``account.move.line``, so it
fires when a LINE is written, not when an ``account.account``'s type is edited
later. Retyping an account to ``off_balance`` when it already carries posted
regular moves therefore drops its movement out of the sections while the other
leg still counts, and the statement is off by exactly that balance. Detecting it
costs a query per run to defend against an act that also makes the Balance Sheet
and Trial Balance wrong, so it is recorded here rather than guarded — the same
call the module makes for the other two divergences it documents. The
reclassification, not the report, is the thing to fix.

DEPRECIATION assumes its offset is an INVESTING account. The add-back is a pure
transfer, so the sections always still sum to the cash movement whatever the
offset is typed. What it assumes is that the credit side (accumulated
depreciation) is typed ``asset_fixed``/``asset_non_current``, which is where a
conventional chart puts it. A chart that offsets depreciation against, say,
``asset_current`` would have that amount shifted out of Operating twice and the
Operating/Investing SPLIT would be wrong — the total, and therefore the
reconciliation, would not be.

DEPRECIATION is the one place the presentation moves money between sections
without changing the total. It is a non-cash expense sitting inside net profit,
matched by a fall in fixed assets. Leaving it in Investing would invent a cash
inflow that never happened, so it is added back in Operating and removed from
Investing — equal and opposite, so the total cannot move.
"""
from dateutil.relativedelta import relativedelta

from odoo import models
from odoo.exceptions import UserError

CASH = ('asset_cash',)

# --- THE CLASSIFICATION. Review this, not the arithmetic. -------------------
OPERATING = (
    'asset_receivable', 'asset_current', 'asset_prepayments',
    'liability_payable', 'liability_current', 'liability_credit_card',
    'income', 'income_other',
    'expense', 'expense_other', 'expense_depreciation', 'expense_direct_cost',
)
INVESTING = ('asset_fixed', 'asset_non_current')
FINANCING = ('liability_non_current', 'equity', 'equity_unaffected')
EXCLUDED = ('off_balance',)

SECTIONS = (('operating', OPERATING), ('investing', INVESTING),
            ('financing', FINANCING))

# The income-statement subset of OPERATING — net profit, the indirect method's
# starting line.
PROFIT_AND_LOSS = ('income', 'income_other', 'expense', 'expense_other',
                   'expense_depreciation', 'expense_direct_cost')


class NcollectionCashFlow(models.TransientModel):
    _name = 'ncollection.account.report.cash.flow'
    # Comparison mixin first: it supplies _nc_columns()/_nc_list_view_ref(),
    # which the engine declares abstract.
    _inherit = ['ncollection.account.report.comparison',
                'ncollection.account.report.whole.entity',
                'ncollection.account.report']
    _description = 'NCollection Cash Flow Statement'

    def _nc_report_title(self):
        return self.env._("Cash Flow Statement")

    def _nc_label_column(self):
        # FPA §Cash Flow names the first column "Category", not "Account".
        return self.env._("Category")

    def _nc_report_action_ref(self):
        # Unique report_name per action — sharing one renders every wizard
        # against the FIRST action's model (see report_templates.xml).
        return 'ncollection_account_reports.action_report_cash_flow'

    # ---- the guards the identity rests on --------------------------------

    def _nc_assert_every_type_is_classified(self):
        """Every ``account_type`` Odoo knows must sit in exactly one bucket."""
        self.ensure_one()
        buckets = (CASH, OPERATING, INVESTING, FINANCING, EXCLUDED)
        known = set().union(*(set(bucket) for bucket in buckets))
        actual = set(dict(
            self.env['account.account']._fields['account_type'].selection))
        missing = sorted(actual - known)
        if missing:
            raise UserError(self.env._(
                "Account type(s) %s are not classified into a cash-flow "
                "section. The statement cannot be produced without deciding "
                "where they belong — an unclassified type would silently break "
                "the reconciliation with cash movements.", ", ".join(missing)))
        overlap = sorted(
            account_type for account_type in actual
            if sum(account_type in bucket for bucket in buckets) > 1)
        if overlap:
            raise UserError(self.env._(
                "Account type(s) %s are classified into more than one "
                "cash-flow section and would be counted twice.",
                ", ".join(overlap)))

    # ---- computation -----------------------------------------------------

    def _nc_at(self, date_from, date_to):
        """This wizard rebound to another window, in memory.

        Same device as ``_nc_comparison_record()``: every other filter travels
        with the dates, so each figure is computed through the exact same engine
        helpers as the current period. Nothing to drift.
        """
        self.ensure_one()
        return self.new({'date_from': date_from, 'date_to': date_to},
                        origin=self)

    def _nc_movement_by_type(self, record):
        """``{account_type: balance movement}`` over ``record``'s period.

        Built on the engine's ``_nc_period_balances()`` rather than a private
        query, so the company / posted-vs-draft filters are the engine's — one
        definition of "which journal items count", not two.
        """
        balances = record._nc_period_balances()
        # active_test=False: an ARCHIVED account can still have moved during the
        # period, and dropping it would break the identity by exactly its
        # movement. Mirrors the engine and the Balance Sheet.
        accounts = self.env['account.account'].with_context(
            active_test=False).browse(list(balances))
        moved = {}
        for account in accounts:
            moved[account.account_type] = (
                moved.get(account.account_type, 0.0) + balances[account.id])
        return moved

    def _nc_cash_balance_at(self, date):
        """Cumulative balance on the cash accounts as of ``date``.

        Measured from the cash accounts themselves, NOT from the sections — that
        independence is what makes the reconciliation test a real check rather
        than a restatement of the same sum.
        """
        self.ensure_one()
        record = self._nc_at(date, date)
        balances = record._nc_closing_balances()
        accounts = self.env['account.account'].with_context(
            active_test=False).browse(list(balances))
        return sum(balances[account.id] for account in accounts
                   if account.account_type in CASH)

    def _nc_cash_flow_figures(self, record):
        """Every figure the statement renders, for one period."""
        moved = self._nc_movement_by_type(record)

        def total(types):
            return sum(moved.get(account_type, 0.0) for account_type in types)

        # The cash effect of a section is the NEGATIVE of its balance movement:
        # a rise in receivables consumes cash, a rise in payables provides it.
        figures = {name: -total(types) for name, types in SECTIONS}

        depreciation = moved.get('expense_depreciation', 0.0)
        figures['operating'] += depreciation
        figures['investing'] -= depreciation

        figures['depreciation'] = depreciation
        figures['net_profit'] = -total(PROFIT_AND_LOSS)
        # Whatever operating cash is left once profit and the depreciation
        # add-back are accounted for: the working-capital swing. Derived, so it
        # cannot disagree with the section total above it.
        figures['working_capital'] = (
            figures['operating'] - figures['net_profit'] - depreciation)
        figures['net_change'] = (figures['operating'] + figures['investing']
                                 + figures['financing'])
        # Measured off SELF, not off `record`: `record` may already be the
        # in-memory comparison copy, and `new(origin=<in-memory record>)` is not
        # a supported nesting. Only the dates differ between the two, and those
        # are passed explicitly — so the figures are identical either way.
        figures['cash_open'] = self._nc_cash_balance_at(
            record.date_from - relativedelta(days=1))
        figures['cash_close'] = self._nc_cash_balance_at(record.date_to)
        return figures

    def _nc_compute_lines(self):
        self.ensure_one()
        self._nc_assert_whole_entity()
        self._nc_assert_every_type_is_classified()
        current = self._nc_cash_flow_figures(self)
        comparison = self._nc_comparison_record()
        previous = (self._nc_cash_flow_figures(comparison)
                    if comparison is not None else {})

        def row(label, key, level=1):
            return self._nc_comparison_row(
                label, current.get(key, 0.0), previous.get(key, 0.0),
                level=level)

        def header(label):
            return self._nc_comparison_row(label, 0.0, 0.0, level=0)

        return [
            header(self.env._("OPERATING ACTIVITIES")),
            row(self.env._("Net profit for the period"), 'net_profit', 2),
            row(self.env._("Add back: depreciation (non-cash)"),
                'depreciation', 2),
            row(self.env._("Movement in working capital"),
                'working_capital', 2),
            row(self.env._("Net cash from operating activities"), 'operating'),
            header(self.env._("INVESTING ACTIVITIES")),
            row(self.env._("Net cash from investing activities"), 'investing'),
            header(self.env._("FINANCING ACTIVITIES")),
            row(self.env._("Net cash from financing activities"), 'financing'),
            row(self.env._("NET CHANGE IN CASH"), 'net_change', 0),
            # The reconciliation, on the face of the statement: these two cash
            # balances are measured from the cash accounts, and their difference
            # is the line above.
            row(self.env._("Cash at the beginning of the period"), 'cash_open'),
            row(self.env._("Cash at the end of the period"), 'cash_close'),
        ]

    def _nc_totals(self):
        """The headline figures as a dict, for tests and for F3 dashboards that
        want them without parsing rendered rows.

        Runs its own aggregates — it does NOT reuse a previous
        ``_nc_compute_lines()`` result. A caller that wants both should hold the
        result rather than call this per KPI.
        """
        self.ensure_one()
        self._nc_assert_whole_entity()
        self._nc_assert_every_type_is_classified()
        return self._nc_cash_flow_figures(self)
