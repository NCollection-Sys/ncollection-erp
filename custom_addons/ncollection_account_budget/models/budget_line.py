# -*- coding: utf-8 -*-
"""F4-T02: a budget line — a GL account, a dimension split, an amount.

``analytic_distribution``, NOT a single analytic FK. FPA's Budget-vs-Actual
spec filters by **Department AND Cost Center** at once, #120 [F4-T01] made those
independent root ``account.analytic.plan`` records, and Odoo validates the 100%
rule per root plan — so one line can carry both. OCA's ``account_budget_oca``
models this as one ``analytic_account_id`` per line and therefore cannot express
it, which is the concrete reason this module was built rather than adopted.

Keying budgets the same way the ACTUALS are keyed is the whole point: the
variance is then a comparison of like with like, computed from
``account.analytic.line`` on one side and budget lines on the other, without a
translation layer in between that could drift.
"""
from odoo import api, fields, models
from odoo.exceptions import UserError

# What "actual" means for a budget line: posted journal items only. A draft
# invoice is an intention, and a budget that counted it would report spending
# that has not happened.
POSTED = ('posted',)


class NcollectionAccountBudgetLine(models.Model):
    _name = 'ncollection.account.budget.line'
    _description = 'NCollection Budget Line'
    _order = 'budget_id, account_id'

    budget_id = fields.Many2one(
        'ncollection.account.budget', required=True, ondelete='cascade',
        index=True)
    company_id = fields.Many2one(related='budget_id.company_id', store=True)
    account_id = fields.Many2one(
        'account.account', required=True, index=True,
        help="The financial account this line budgets.")
    # Same mechanism the actuals use, so budget and actual are keyed alike.
    analytic_distribution = fields.Json()
    amount = fields.Monetary(required=True, currency_field='currency_id')
    currency_id = fields.Many2one(
        related='company_id.currency_id', readonly=True)
    name = fields.Char()

    _amount_not_zero = models.Constraint(
        'CHECK(amount <> 0)',
        'A budget line of zero budgets nothing — leave it out instead.',
    )

    @api.constrains('budget_id')
    def _check_budget_editable(self):
        """An approved budget is a decision; its lines stop moving.

        Without this, "approved" would be a label rather than a state, and the
        audit history would record an approval of something that later changed.
        """
        for line in self:
            if line.budget_id.state != 'draft':
                raise UserError(self.env._(
                    "%(budget)s is %(state)s — only a draft budget's lines can "
                    "be changed. Revise it to make a new version.",
                    budget=line.budget_id.name, state=line.budget_id.state))

    # ---- actuals ---------------------------------------------------------

    @api.model
    def _nc_actuals(self, account_ids, date_from, date_to):
        """``{account_id: actual movement}`` over the window, posted only.

        Signed as the ledger holds it — the caller decides presentation. Kept
        deliberately separate from the budget side so the two figures are
        computed independently and a test comparing them is a real check rather
        than a restatement.
        """
        rows = self.env['account.move.line']._read_group(
            [('parent_state', 'in', list(POSTED)),
             ('company_id', '=', self.env.company.id),
             ('account_id', 'in', list(account_ids)),
             ('date', '>=', date_from), ('date', '<=', date_to)],
            groupby=['account_id'], aggregates=['balance:sum'])
        return {account.id: balance or 0.0 for account, balance in rows
                if account}
