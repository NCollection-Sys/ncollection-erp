"""Transactions of a bank / cash journal: one row per business operation (docs/markdown/JOURNAL_OPERATIONS.md §2).

1. posted payments of the journal (in process or paid);
2. posted statement lines that are NOT the settlement of one of those payments (a line reconciled with a line of a
   payment of the same journal, or created by Mark as Paid) — a matched payment stays ONE row;
3. any other posted line on the journal's liquidity account (opening balances, manual entries).

``liquidity_balance`` is what reached the journal's liquidity account, in company currency; ``running_balance`` is its
window sum over the whole journal, so the last row equals the ledger balance. Read only; nothing is stored.
"""
from odoo import fields, models, tools


class JournalTransaction(models.Model):
    _name = 'ncollection.journal.transaction'
    _description = 'Bank and Cash Journal Transaction'
    _auto = False
    _order = 'date desc, id desc'
    _rec_name = 'reference'

    journal_id = fields.Many2one('account.journal', readonly=True)
    company_id = fields.Many2one('res.company', readonly=True)
    company_currency_id = fields.Many2one('res.currency', string='Company Currency', related='company_id.currency_id')
    date = fields.Date(readonly=True)
    reference = fields.Char(readonly=True)
    description = fields.Char(readonly=True)
    operation = fields.Selection([
        ('receipt', 'Receipt'), ('payment', 'Payment'), ('transfer', 'Internal Transfer'),
        ('account', 'Account'), ('bank', 'Bank Transaction'), ('entry', 'Journal Entry')],
        string='Type', readonly=True)
    direction = fields.Selection([('in', 'Incoming'), ('out', 'Outgoing')], readonly=True)
    partner_id = fields.Many2one('res.partner', string='Customer / Supplier', readonly=True)
    account_id = fields.Many2one('account.account', readonly=True)
    payment_method_line_id = fields.Many2one('account.payment.method.line', string='Method', readonly=True)
    currency_id = fields.Many2one('res.currency', readonly=True)
    amount = fields.Monetary(currency_field='currency_id', readonly=True)
    amount_in = fields.Monetary(string='Incoming', currency_field='currency_id', readonly=True)
    amount_out = fields.Monetary(string='Outgoing', currency_field='currency_id', readonly=True)
    pending_balance = fields.Monetary(string='Pending', currency_field='company_currency_id', readonly=True)
    liquidity_balance = fields.Monetary(string='In Account', currency_field='company_currency_id', readonly=True)
    running_balance = fields.Monetary(currency_field='company_currency_id', readonly=True)
    status = fields.Selection([
        ('in_process', 'In Process'), ('awaiting_bank', 'Paid, awaiting bank'), ('paid', 'Paid'),
        ('reconciled', 'Reconciled'),
        ('to_reconcile', 'To Reconcile'), ('posted', 'Posted')], readonly=True)
    payment_id = fields.Many2one('account.payment', readonly=True)
    statement_line_id = fields.Many2one('account.bank.statement.line', string='Bank Transaction', readonly=True)
    move_id = fields.Many2one('account.move', string='Journal Entry', readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(f"""
CREATE OR REPLACE VIEW {self._table} AS (
WITH journals AS (
    SELECT j.id, j.company_id, j.default_account_id, c.transfer_account_id
      FROM account_journal j JOIN res_company c ON c.id = j.company_id
     WHERE j.type IN ('bank', 'cash')
),
pay AS (
    SELECT p.id, p.journal_id, j.company_id, m.date, m.name AS reference, p.memo AS description,
           CASE WHEN p.nc_operation = 'transfer' OR p.destination_account_id = j.transfer_account_id THEN 'transfer'
                WHEN p.nc_counterpart_type = 'account' THEN 'account'
                WHEN p.payment_type = 'inbound' THEN 'receipt' ELSE 'payment' END AS operation,
           CASE WHEN p.payment_type = 'inbound' THEN 'in' ELSE 'out' END AS direction,
           p.partner_id, p.destination_account_id AS account_id, p.payment_method_line_id, p.currency_id,
           CASE WHEN p.payment_type = 'inbound' THEN p.amount ELSE -p.amount END AS amount,
           p.amount_company_currency_signed AS company_amount,
           COALESCE((SELECT SUM(l.amount_residual)
                       FROM account_move_line l JOIN account_account a ON a.id = l.account_id
                      WHERE l.move_id = m.id AND a.reconcile IS TRUE AND l.reconciled IS NOT TRUE
                        AND l.account_id IS DISTINCT FROM j.default_account_id
                        AND l.account_id IS DISTINCT FROM p.destination_account_id
                        AND l.account_id IS DISTINCT FROM j.transfer_account_id
                        AND a.account_type NOT IN ('asset_receivable', 'liability_payable')), 0) AS pending,
           p.state AS state, m.id AS move_id
      FROM account_payment p
      JOIN journals j ON j.id = p.journal_id
      JOIN account_move m ON m.id = p.move_id AND m.state = 'posted'
     WHERE p.state IN ('in_process', 'paid')
),
st AS (
    SELECT s.id, s.journal_id, j.company_id, m.date, m.name AS reference, s.payment_ref AS description,
           s.partner_id, s.amount,
           (SELECT COALESCE(SUM(l.balance), 0) FROM account_move_line l
             WHERE l.move_id = m.id AND l.account_id = j.default_account_id) AS liquidity,
           s.is_reconciled, m.id AS move_id, m.currency_id
      FROM account_bank_statement_line s
      JOIN journals j ON j.id = s.journal_id
      JOIN account_move m ON m.id = s.move_id AND m.state = 'posted'
     WHERE s.nc_settlement_payment_id IS NULL
       AND NOT EXISTS (
           SELECT 1
             FROM account_move_line sl
             JOIN account_partial_reconcile pr ON sl.id IN (pr.debit_move_id, pr.credit_move_id)
             JOIN account_move_line ol ON ol.id = CASE WHEN pr.debit_move_id = sl.id
                                                       THEN pr.credit_move_id ELSE pr.debit_move_id END
             JOIN account_payment op ON op.id = ol.payment_id
            WHERE sl.move_id = m.id AND op.journal_id = s.journal_id)
),
misc AS (
    SELECT l.id, j.id AS journal_id, l.company_id, l.date, m.name AS reference, l.name AS description,
           l.partner_id, l.amount_currency, l.currency_id, l.balance, m.id AS move_id
      FROM account_move_line l
      JOIN journals j ON j.default_account_id = l.account_id AND j.company_id = l.company_id
      JOIN account_move m ON m.id = l.move_id
     WHERE l.parent_state = 'posted' AND l.payment_id IS NULL AND l.statement_line_id IS NULL
       AND (m.journal_id = j.id OR NOT EXISTS (
            SELECT 1 FROM account_journal j2 WHERE j2.id = m.journal_id AND j2.default_account_id = l.account_id))
),
rows AS (
    SELECT pay.id * 10 + 1 AS id, journal_id, company_id, date, reference, description, operation, direction,
           partner_id, account_id, payment_method_line_id, currency_id, amount,
           pending AS pending_balance, company_amount - pending AS liquidity_balance,
           CASE WHEN state = 'paid' AND pending <> 0 THEN 'awaiting_bank' ELSE state END AS status,
           pay.id AS payment_id, NULL::integer AS statement_line_id, move_id
      FROM pay
    UNION ALL
    SELECT st.id * 10 + 2, journal_id, company_id, date, reference, description, 'bank',
           CASE WHEN amount >= 0 THEN 'in' ELSE 'out' END, partner_id, NULL, NULL, currency_id, amount,
           0, liquidity, CASE WHEN is_reconciled THEN 'reconciled' ELSE 'to_reconcile' END,
           NULL, st.id, move_id
      FROM st
    UNION ALL
    SELECT misc.id * 10 + 3, journal_id, company_id, date, reference, description, 'entry',
           CASE WHEN balance >= 0 THEN 'in' ELSE 'out' END, partner_id, NULL, NULL, currency_id,
           amount_currency, 0, balance, 'posted', NULL, NULL, move_id
      FROM misc
)
SELECT rows.*,
       CASE WHEN rows.amount > 0 THEN rows.amount ELSE 0 END AS amount_in,
       CASE WHEN rows.amount < 0 THEN -rows.amount ELSE 0 END AS amount_out,
       SUM(rows.liquidity_balance) OVER (PARTITION BY rows.journal_id ORDER BY rows.date, rows.id
                                         ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_balance
  FROM rows
)""")

    def action_open_source(self):
        self.ensure_one()
        record = self.payment_id or self.statement_line_id.move_id or self.move_id
        return {'type': 'ir.actions.act_window', 'res_model': record._name, 'res_id': record.id,
                'view_mode': 'form', 'target': 'current'}

    def action_open_move(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'account.move', 'res_id': self.move_id.id,
                'view_mode': 'form', 'target': 'current'}
