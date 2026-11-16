"""A bank file imported AFTER Mark as Paid must not add the same money a second time.

A file line in the same journal, with exactly the same amount, dated within ``WINDOW_DAYS`` of a Mark-as-Paid
settlement the bank has not confirmed yet, takes that settlement's ``unique_import_id``. The OCA importer already skips
a line whose id exists (and keeps the statement's opening balance right), and the preview shows it as "already
imported", so the user sees it before pressing Import. Each settlement is consumed once, in date order.
"""

from odoo import fields, models

WINDOW_DAYS = 7


class AccountStatementImport(models.TransientModel):
    _inherit = 'account.statement.import'

    def _complete_stmts_vals(self, stmts_vals, journal, account_number):
        stmts_vals = super()._complete_stmts_vals(stmts_vals, journal, account_number)
        currency = journal.currency_id or journal.company_id.currency_id
        settlements = self.env['account.bank.statement.line'].search([
            ('journal_id', '=', journal.id), ('nc_settlement_payment_id', '!=', False),
            ('nc_bank_confirmed', '=', False)], order='date, id')
        if not settlements:
            return stmts_vals
        used = self.env['account.bank.statement.line']
        for st_vals in stmts_vals:
            for lvals in st_vals['transactions']:
                amount = float(lvals.get('amount') or 0.0)
                day = fields.Date.to_date(lvals.get('date'))
                for settlement in settlements - used:
                    if (currency.compare_amounts(settlement.amount, amount) == 0 and day
                            and abs((settlement.date - day).days) <= WINDOW_DAYS):
                        used |= settlement
                        lvals['unique_import_id'] = settlement.unique_import_id
                        break
        # The preview parses with ``nc_skipped_rows`` in the context and must not write anything.
        if used and 'nc_skipped_rows' not in self.env.context:
            used.write({'nc_bank_confirmed': True})
        return stmts_vals
