"""Company accounts the journal operations need and Odoo has no field for.

* ``nc_payment_suspense_account_id``: where a Payment / Receipt posts its counterpart when the payee is not known yet
  (no Customer / Supplier chosen, or Type Account with no account). It is NOT the bank journal's Suspense account
  (Odoo's holding account for unmatched statement lines), NOT Outstanding Receipts / Payments, NOT the transfer
  clearing account, and never a receivable / payable or liquidity account: it is a reconcilable balance-sheet account
  that is cleared later by a reclassification entry reconciled against it.
* ``nc_bank_fee_account_id``: the expense account an Internal Transfer posts its bank charge to (Request 13), apart
  from Odoo's exchange gain / loss accounts.
"""
from odoo import api, fields, models
from odoo.exceptions import ValidationError

SUSPENSE_TYPES = ('asset_current', 'liability_current')
FEE_TYPES = ('expense', 'expense_direct_cost')


class ResCompany(models.Model):
    _inherit = 'res.company'

    nc_payment_suspense_account_id = fields.Many2one(
        'account.account', string='Unidentified Payments (Suspense)', check_company=True,
        domain="[('account_type', 'in', %s), ('reconcile', '=', True)]" % (list(SUSPENSE_TYPES),),
        help="Counterpart of a payment or receipt whose payee is not known yet. Clear it later with a "
             "reclassification entry reconciled against the suspense line. Not the bank Suspense account.")
    nc_bank_fee_account_id = fields.Many2one(
        'account.account', string='Bank Fees', check_company=True,
        domain="[('account_type', 'in', %s)]" % (list(FEE_TYPES),),
        help="Expense account for bank charges entered on an internal transfer.")

    @api.constrains('nc_payment_suspense_account_id', 'nc_bank_fee_account_id', 'transfer_account_id',
                    'account_journal_suspense_account_id')
    def _nc_check_operation_accounts(self):
        for company in self:
            suspense = company.nc_payment_suspense_account_id
            if suspense:
                if suspense.account_type not in SUSPENSE_TYPES or not suspense.reconcile:
                    raise ValidationError(self.env._(
                        "%(account)s cannot hold unidentified payments: use a reconcilable current asset or current "
                        "liability account (never a receivable, payable or bank account).",
                        account=suspense.display_name))
                if suspense in (company.transfer_account_id | company.account_journal_suspense_account_id):
                    raise ValidationError(self.env._(
                        "The unidentified-payments account must differ from the transfer account and from the bank "
                        "Suspense account."))
            fee = company.nc_bank_fee_account_id
            if fee and fee.account_type not in FEE_TYPES:
                raise ValidationError(self.env._("%(account)s is not an expense account.", account=fee.display_name))


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    nc_payment_suspense_account_id = fields.Many2one(
        related='company_id.nc_payment_suspense_account_id', readonly=False)
    nc_bank_fee_account_id = fields.Many2one(related='company_id.nc_bank_fee_account_id', readonly=False)
