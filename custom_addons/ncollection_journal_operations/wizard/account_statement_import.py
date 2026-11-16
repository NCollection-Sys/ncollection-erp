"""A bank file imported AFTER Mark as Paid must not add the same money a second time, and must never swallow a
different transaction.

A file line is recognised as a Mark-as-Paid settlement only on a STRONG match: same journal, exactly the same signed
amount, dated within ``WINDOW_DAYS``, AND an identity link — the same partner, or the payment's number / memo found in
the line's label or reference. Amount and date alone are never enough (two customers paying the same amount the same
week stay two transactions). A recognised line takes the settlement's ``unique_import_id``; the OCA importer then
skips it (and keeps the opening balance right), and the preview shows it as already imported before Import is
pressed. The bank's own id of that line is kept on the settlement (``nc_bank_import_id``), so the same file imported
again is still recognised. Each settlement is consumed by one file line.
"""
from odoo import fields, models

WINDOW_DAYS = 7


class AccountStatementImport(models.TransientModel):
    _inherit = 'account.statement.import'

    @staticmethod
    def _nc_identity_match(settlement, lvals):
        payment = settlement.nc_settlement_payment_id
        partner = lvals.get('partner_id')
        if partner and payment.partner_id and partner == payment.partner_id.id:
            return True
        text = ' '.join(str(lvals.get(key) or '') for key in ('payment_ref', 'ref', 'narration')).lower()
        tokens = [token.lower() for token in (payment.name, payment.memo) if token and len(token) >= 4]
        return any(token in text for token in tokens)

    def _complete_stmts_vals(self, stmts_vals, journal, account_number):
        stmts_vals = super()._complete_stmts_vals(stmts_vals, journal, account_number)
        currency = journal.currency_id or journal.company_id.currency_id
        settlements = self.env['account.bank.statement.line'].search([
            ('journal_id', '=', journal.id), ('nc_settlement_payment_id', '!=', False),
            ('move_id.state', '=', 'posted')], order='date, id')
        if not settlements:
            return stmts_vals
        by_bank_id = {st.nc_bank_import_id: st for st in settlements if st.nc_bank_import_id}
        open_settlements = settlements.filtered(lambda st: not st.nc_bank_confirmed)
        used = {}
        for st_vals in stmts_vals:
            for lvals in st_vals['transactions']:
                bank_id = lvals.get('unique_import_id')
                if bank_id and bank_id in by_bank_id:  # the same file again: still the same transaction
                    lvals['unique_import_id'] = by_bank_id[bank_id].unique_import_id
                    continue
                amount = float(lvals.get('amount') or 0.0)
                day = fields.Date.to_date(lvals.get('date'))
                for settlement in open_settlements:
                    if (settlement.id not in used and day
                            and currency.compare_amounts(settlement.amount, amount) == 0
                            and abs((settlement.date - day).days) <= WINDOW_DAYS
                            and self._nc_identity_match(settlement, lvals)):
                        used[settlement.id] = bank_id
                        lvals['unique_import_id'] = settlement.unique_import_id
                        break
        # The preview parses with ``nc_skipped_rows`` in the context (ncollection_bank_reconciliation): no write.
        if used and 'nc_skipped_rows' not in self.env.context:
            for settlement in open_settlements.filtered(lambda st: st.id in used):
                settlement.write({'nc_bank_confirmed': True, 'nc_bank_import_id': used[settlement.id] or False})
        return stmts_vals
