from odoo import fields, models


class AccountBankStatementLine(models.Model):
    _inherit = 'account.bank.statement.line'

    nc_settlement_payment_id = fields.Many2one(
        'account.payment', string='Marked as Paid', copy=False, readonly=True, index='btree_not_null',
        ondelete='restrict',
        help="This line was created by Mark as Paid for that payment. A bank file line of the same amount imported "
             "within 7 days is recognised as this transaction instead of being added again.")
    nc_bank_confirmed = fields.Boolean(
        string='Confirmed by Bank File', copy=False, readonly=True,
        help="A later bank statement import contained this Mark-as-Paid transaction.")
