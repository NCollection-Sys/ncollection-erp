# -*- coding: utf-8 -*-
"""F2-T04: Native Statement of Changes in Equity.

FPA §Financial Statements lists it beside the Cash Flow Statement. Where the
Balance Sheet shows equity as one number at a point in time, this shows how that
number got there: **Opening · Movement · Closing** per component.

WHY THESE THREE COLUMNS AND NOT THE COMPARISON MIXIN'S. The mixin's
``previous_amount`` means "the same figure over a different period". Opening
balance is not that — it is the same component at an earlier *instant*. Reusing
the comparison fields to carry it would put a number under a heading that means
something else, which is the kind of quiet lie that survives review. The engine
already has ``opening_balance`` / ``balance`` / ``closing_balance`` and they mean
exactly what is wanted, so this report inherits the engine alone.

COMPONENTS ARE ODOO'S CLASSIFICATION, NOT OURS. ``internal_group`` decides what
an income-statement account is (Odoo computes it from ``account_type``), so a
localisation adding a type — ``expense_other`` is already one such — lands in the
right component without this file being edited. The engine makes the same choice
for the same reason (see ``_nc_opening_balances``).

TIES TO THE BALANCE SHEET. FPA's acceptance is that the statement ties to the BS
equity section, and the test asserts exactly that against
``balance_sheet._nc_bucket_totals()`` — an independent computation, not a
restatement of this one. Both are cumulative-to-date and both negate the
credit-normal balances, so a positive figure means equity.

KNOWN, MEASURED DIVERGENCE. The Balance Sheet's ``accumulated_earnings`` bucket
is a deliberate 1:1 transcription of the mis_builder template it replaces during
the #117 transition, and that template's expression omits ``expense_other``.
This statement does not omit it, because a statement of equity that drops a
class of expense is wrong. So on books that use an ``expense_other`` account the
two differ by exactly that account's balance — asserted as such in
``test_cash_flow_equity.py`` rather than left to be discovered. Reconciling them
means editing the mis parity map, which belongs to #117, not here.
"""
from dateutil.relativedelta import relativedelta

from odoo import models

# (key, label, sign, matcher) — sign -1 presents credit-normal equity as a
# positive figure, exactly as the Balance Sheet's equity buckets do.
_EQUITY_COMPONENTS = (
    ('share_capital', "Share Capital & Reserves", -1,
     lambda account: account.account_type == 'equity'),
    ('retained_earnings', "Retained Earnings", -1,
     lambda account: account.account_type == 'equity_unaffected'),
    # Its MOVEMENT column is the profit or loss for the period; its opening is
    # the result accumulated before the period began.
    ('accumulated_result', "Accumulated Result", -1,
     lambda account: account.internal_group in ('income', 'expense')),
)


class NcollectionEquityChanges(models.TransientModel):
    _name = 'ncollection.account.report.equity.changes'
    _inherit = ['ncollection.account.report.whole.entity',
                'ncollection.account.report']
    _description = 'NCollection Statement of Changes in Equity'

    def _nc_report_title(self):
        return self.env._("Statement of Changes in Equity")

    def _nc_report_action_ref(self):
        # Unique report_name per action — sharing one renders every wizard
        # against the FIRST action's model (see report_templates.xml).
        return 'ncollection_account_reports.action_report_equity_changes'

    def _nc_list_view_ref(self):
        return 'ncollection_account_reports.view_report_line_equity_list'

    def _nc_columns(self):
        return [
            {'key': 'label', 'label': self.env._("Component"), 'type': 'char'},
            {'key': 'opening_balance', 'label': self.env._("Opening Balance"),
             'type': 'monetary'},
            {'key': 'balance', 'label': self.env._("Movement"),
             'type': 'monetary'},
            {'key': 'closing_balance', 'label': self.env._("Closing Balance"),
             'type': 'monetary'},
        ]

    def _nc_move_line_domain(self, account=None, partner=None):
        """Drill-down must show the journal items the DISPLAYED figure is made
        of. The headline column here is the cumulative Closing balance, so — as
        on the Balance Sheet, and for the same reason — bound on the closing
        date only. The engine's period default would open a list whose total
        silently excludes everything before ``date_from``.
        """
        return self._nc_filter_domain(account=account, partner=partner) + [
            ('date', '<=', self.date_to)]

    # ---- computation -----------------------------------------------------

    def _nc_at(self, date):
        """This wizard rebound to "cumulative as of ``date``", in memory.

        Same device as ``_nc_comparison_record()``: every other filter travels
        with the date, so the opening and closing figures come out of the exact
        same engine helper. Nothing to drift.
        """
        self.ensure_one()
        return self.new({'date_from': date, 'date_to': date}, origin=self)

    def _nc_equity_balances(self, date):
        """``(by_component, by_account)`` of signed equity as of ``date``.

        ``by_component`` is ``{key: total}``; ``by_account`` is
        ``{account: signed balance}`` for the detail rows.
        """
        self.ensure_one()
        balances = self._nc_at(date)._nc_closing_balances()
        # active_test=False: an ARCHIVED account can still carry equity, and
        # dropping it would understate the total. Mirrors the Balance Sheet.
        accounts = self.env['account.account'].with_context(
            active_test=False).browse(list(balances))
        by_component = {}
        by_account = {}
        for key, _label, sign, matches in _EQUITY_COMPONENTS:
            rows = [(account, balances[account.id] * sign)
                    for account in accounts if matches(account)]
            by_component[key] = sum(balance for _account, balance in rows)
            by_account.update({account: balance for account, balance in rows})
        return by_component, by_account

    def _nc_equity_figures(self):
        """``{key: (opening, movement, closing)}`` plus a ``total`` entry."""
        self.ensure_one()
        # Opening is the CLOSING position of the day before the period starts —
        # so opening + movement == closing by construction, with no separate
        # period query that could disagree with the two cumulative ones.
        opening, opening_by_account = self._nc_equity_balances(
            self.date_from - relativedelta(days=1))
        closing, closing_by_account = self._nc_equity_balances(self.date_to)
        figures = {key: (opening.get(key, 0.0),
                         closing.get(key, 0.0) - opening.get(key, 0.0),
                         closing.get(key, 0.0))
                   for key, _label, _sign, _matches in _EQUITY_COMPONENTS}
        figures['total'] = tuple(sum(values) for values in zip(*(
            figures[key] for key, _l, _s, _m in _EQUITY_COMPONENTS)))
        return figures, opening_by_account, closing_by_account

    def _nc_row(self, label, values, level=0, account_id=False):
        opening, movement, closing = values
        return {
            'label': label,
            'account_id': account_id,
            'opening_balance': opening,
            # `balance` keeps the generic engine surfaces (drill-down list,
            # anything reading the common column) meaningful for this report.
            'balance': movement,
            'closing_balance': closing,
            'level': level,
        }

    def _nc_compute_lines(self):
        self.ensure_one()
        self._nc_assert_whole_entity()
        figures, opening_by_account, closing_by_account = self._nc_equity_figures()

        rows = [self._nc_row(self.env._("EQUITY"), (0.0, 0.0, 0.0), level=0)]
        for key, label, _sign, matches in _EQUITY_COMPONENTS:
            # Plain string, like the Balance Sheet's and P&L's section labels:
            # a lookup-table label is not a literal, and `env._()` over a
            # variable extracts nothing for translation anyway.
            rows.append(self._nc_row(label, figures[key], level=1))
            # Union of both instants: an account closed DURING the period still
            # belongs on the statement — its money is inside the Opening
            # subtotal either way, so omitting its row would make the visible
            # rows fail to add up to the subtotal above them.
            accounts = {account for account in
                        set(opening_by_account) | set(closing_by_account)
                        if matches(account)}
            for account in sorted(accounts, key=lambda a: a.code or ''):
                opening = opening_by_account.get(account, 0.0)
                closing = closing_by_account.get(account, 0.0)
                rows.append(self._nc_row(
                    account.display_name, (opening, closing - opening, closing),
                    level=2, account_id=account.id))
        rows.append(self._nc_row(
            self.env._("TOTAL EQUITY"), figures['total'], level=0))
        return rows

    def _nc_totals(self):
        """``(opening, movement, closing)`` total equity — for tests and for F3
        dashboards that want the headline figures without parsing rendered rows.

        Runs its own aggregates — it does NOT reuse a previous
        ``_nc_compute_lines()`` result. A caller that wants both should hold the
        result rather than call this per KPI.
        """
        self.ensure_one()
        self._nc_assert_whole_entity()
        return self._nc_equity_figures()[0]['total']
