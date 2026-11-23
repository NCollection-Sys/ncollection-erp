"""Transactions of a bank / cash journal: one row per business operation (docs/markdown/JOURNAL_OPERATIONS.md §2).

1. posted payments of the journal;
2. posted statement lines: a plain bank transaction; a "Bank Confirmation" (one that settles a posted payment of the
   same journal: Mark as Paid, or matched with the payment's outstanding line) is FOLDED into that payment's row - its
   booked amount moves the running balance on the payment's own row and it is not listed again (19.0.1.3.0);
3. any other posted line on the journal's liquidity account (opening balances, manual entries).

Every posted line on the journal's account belongs to exactly one row, and ``liquidity_balance`` is the SUM OF THOSE
POSTED LINES (company currency), never a nominal payment amount: a bank line matched for a different amount, a
partial match or a write-off counts exactly what was booked, on its own date. ``running_balance`` is the window sum in
(value_date, id) order of the balance effect in the journal's balance currency; ``value_date`` is the bank date
(the day the money moved on the account) or, while nothing moved, the document date. So the last row equals the card's Current
Balance and the row at any value date equals the ledger balance at that date. One documented exception: a payment
confirmed in several bank lines on different days counts on the LAST of those days. Read only; nothing is stored.
"""
from odoo import fields, models, tools


class JournalTransaction(models.Model):
    _name = 'ncollection.journal.transaction'
    _description = 'Bank and Cash Journal Transaction'
    _auto = False
    # newest first by the date the money moved (the running balance is computed oldest first on the same key)
    _order = 'value_date desc, id desc'
    _rec_name = 'reference'

    journal_id = fields.Many2one('account.journal', readonly=True)
    company_id = fields.Many2one('res.company', readonly=True)
    company_currency_id = fields.Many2one('res.currency', string='Company Currency', related='company_id.currency_id')
    date = fields.Date(readonly=True)
    reference = fields.Char(readonly=True)
    description = fields.Char(readonly=True)
    operation = fields.Selection([
        ('receipt', 'Receipt'), ('payment', 'Payment'), ('transfer', 'Internal Transfer'),
        ('account', 'Account'), ('bank', 'Bank Transaction'), ('settlement', 'Bank Confirmation'),
        ('entry', 'Journal Entry')],
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
    balance_currency_id = fields.Many2one('res.currency', string='Balance Currency', readonly=True)
    bank_date = fields.Date(string='Bank Date', readonly=True,
                            help='When the money moved on the account: the payment date when it posts directly to the '
                                 'bank / cash account, else the date of the bank line that confirmed it. Empty while '
                                 'the payment is still in process.')
    value_date = fields.Date(string='Value Date', readonly=True)
    balance_amount = fields.Monetary(string='Balance Effect', currency_field='balance_currency_id', readonly=True)
    balance_approx = fields.Boolean(readonly=True)
    running_balance = fields.Monetary(currency_field='balance_currency_id', readonly=True,
                                      help='Posted ledger balance of the account after this row, in the currency of '
                                           'the Current Balance on the journal card.')
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
    SELECT j.id, j.company_id, j.default_account_id, c.transfer_account_id,
           -- the currency the card's Current Balance is shown in: the journal's own, else the company's
           COALESCE(j.currency_id, c.currency_id) AS balance_currency_id,
           (j.currency_id IS NOT NULL AND j.currency_id <> c.currency_id) AS foreign_journal
      FROM account_journal j JOIN res_company c ON c.id = j.company_id
     WHERE j.type IN ('bank', 'cash')
),
pay AS (
    SELECT p.id, p.journal_id, j.company_id, j.balance_currency_id, m.date, m.name AS reference, p.memo AS description,
           CASE WHEN p.nc_operation = 'transfer' OR p.destination_account_id = j.transfer_account_id THEN 'transfer'
                WHEN p.nc_counterpart_type = 'account' THEN 'account'
                WHEN p.payment_type = 'inbound' THEN 'receipt' ELSE 'payment' END AS operation,
           CASE WHEN p.payment_type = 'inbound' THEN 'in' ELSE 'out' END AS direction,
           p.partner_id, p.destination_account_id AS account_id, p.payment_method_line_id, p.currency_id,
           CASE WHEN p.payment_type = 'inbound' THEN p.amount ELSE -p.amount END AS amount,
           (SELECT COALESCE(SUM(l.balance), 0) FROM account_move_line l
             WHERE l.move_id = m.id AND l.account_id = j.default_account_id) AS liquidity,
           (SELECT COALESCE(SUM(CASE WHEN j.foreign_journal AND l.currency_id = j.balance_currency_id
                                     THEN l.amount_currency ELSE l.balance END), 0) FROM account_move_line l
             WHERE l.move_id = m.id AND l.account_id = j.default_account_id) AS liquidity_jc,
           EXISTS (SELECT 1 FROM account_move_line l WHERE l.move_id = m.id AND l.account_id = j.default_account_id
                      AND j.foreign_journal AND l.currency_id <> j.balance_currency_id) AS approx,
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
),
st AS (
    -- Every posted statement line. One that settles a payment of the same journal (Mark as Paid, or matched with
    -- the payment's outstanding line) is a "settlement": its own date, its own posted amount, linked to the payment.
    SELECT s.id, s.journal_id, j.company_id, j.balance_currency_id, m.date, m.name AS reference,
           s.payment_ref AS description,
           s.partner_id, s.amount,
           (SELECT COALESCE(SUM(l.balance), 0) FROM account_move_line l
             WHERE l.move_id = m.id AND l.account_id = j.default_account_id) AS liquidity,
           (SELECT COALESCE(SUM(CASE WHEN j.foreign_journal AND l.currency_id = j.balance_currency_id
                                     THEN l.amount_currency ELSE l.balance END), 0) FROM account_move_line l
             WHERE l.move_id = m.id AND l.account_id = j.default_account_id) AS liquidity_jc,
           EXISTS (SELECT 1 FROM account_move_line l WHERE l.move_id = m.id AND l.account_id = j.default_account_id
                      AND j.foreign_journal AND l.currency_id <> j.balance_currency_id) AS approx,
           s.is_reconciled, m.id AS move_id, m.currency_id,
           COALESCE(s.nc_settlement_payment_id, (
               SELECT MIN(op.id)
                 FROM account_move_line sl
                 JOIN account_partial_reconcile pr ON sl.id IN (pr.debit_move_id, pr.credit_move_id)
                 JOIN account_move_line ol ON ol.id = CASE WHEN pr.debit_move_id = sl.id
                                                           THEN pr.credit_move_id ELSE pr.debit_move_id END
                 JOIN account_payment op ON op.id = ol.payment_id
                WHERE sl.move_id = m.id AND op.journal_id = s.journal_id)) AS settled_payment_id
      FROM account_bank_statement_line s
      JOIN journals j ON j.id = s.journal_id
      JOIN account_move m ON m.id = s.move_id AND m.state = 'posted'
),
misc AS (
    SELECT l.id, j.id AS journal_id, l.company_id, j.balance_currency_id, l.date, m.name AS reference,
           l.name AS description, l.partner_id, l.amount_currency, l.currency_id, l.balance,
           CASE WHEN j.foreign_journal AND l.currency_id = j.balance_currency_id THEN l.amount_currency
                ELSE l.balance END AS balance_jc,
           (j.foreign_journal AND l.currency_id <> j.balance_currency_id) AS approx, m.id AS move_id
      FROM account_move_line l
      JOIN journals j ON j.default_account_id = l.account_id AND j.company_id = l.company_id
      JOIN account_move m ON m.id = l.move_id
     WHERE l.parent_state = 'posted' AND l.payment_id IS NULL AND l.statement_line_id IS NULL
       AND (m.journal_id = j.id OR NOT EXISTS (
            SELECT 1 FROM account_journal j2 WHERE j2.id = m.journal_id AND j2.default_account_id = l.account_id))
),
-- A "Bank Confirmation" (a posted statement line that settles a posted payment of the same journal) is the bank
-- confirming money that payment already stands for. It is FOLDED into that payment's row: its booked amount is added
-- to the payment's liquidity, its date becomes the payment's bank date, and it is not a row of its own. So a payment
-- that turns Paid moves the running balance on its own row, never on a hidden technical row and never twice.
settle AS (
    SELECT st.settled_payment_id AS payment_id, SUM(st.liquidity) AS liquidity, SUM(st.liquidity_jc) AS liquidity_jc,
           BOOL_OR(st.approx) AS approx, MAX(st.date) AS bank_date, MIN(st.id) AS statement_line_id
      FROM st JOIN pay ON pay.id = st.settled_payment_id
     GROUP BY st.settled_payment_id
),
rows AS (
    SELECT pay.id * 10 + 1 AS id, pay.journal_id, pay.company_id, pay.balance_currency_id, pay.date,
           COALESCE(settle.bank_date, CASE WHEN pay.liquidity <> 0 THEN pay.date END) AS bank_date,
           reference, description, operation, direction,
           partner_id, account_id, payment_method_line_id, currency_id, amount,
           pending AS pending_balance, pay.liquidity + COALESCE(settle.liquidity, 0) AS liquidity_balance,
           pay.liquidity_jc + COALESCE(settle.liquidity_jc, 0) AS balance_amount,
           pay.approx OR COALESCE(settle.approx, FALSE) AS balance_approx,
           CASE WHEN state = 'paid' AND pending <> 0 THEN 'awaiting_bank' ELSE state END AS status,
           pay.id AS payment_id, settle.statement_line_id, move_id
      FROM pay LEFT JOIN settle ON settle.payment_id = pay.id
    UNION ALL
    -- statement lines that confirm no posted payment of this journal: ordinary bank transactions
    SELECT st.id * 10 + 2, journal_id, company_id, balance_currency_id, date, date, reference, description,
           'bank', CASE WHEN amount >= 0 THEN 'in' ELSE 'out' END, partner_id, NULL, NULL, currency_id, amount,
           0, liquidity, liquidity_jc, approx, CASE WHEN is_reconciled THEN 'reconciled' ELSE 'to_reconcile' END,
           NULL, st.id, move_id
      FROM st
     WHERE NOT EXISTS (SELECT 1 FROM pay WHERE pay.id = st.settled_payment_id)
    UNION ALL
    SELECT misc.id * 10 + 3, journal_id, company_id, balance_currency_id, date, date, reference, description,
           'entry', CASE WHEN balance >= 0 THEN 'in' ELSE 'out' END, partner_id, NULL, NULL, currency_id,
           amount_currency, 0, balance, balance_jc, approx, 'posted', NULL, NULL, move_id
      FROM misc
)
SELECT rows.*, COALESCE(rows.bank_date, rows.date) AS value_date,
       CASE WHEN rows.amount > 0 THEN rows.amount ELSE 0 END AS amount_in,
       CASE WHEN rows.amount < 0 THEN -rows.amount ELSE 0 END AS amount_out,
       -- Chronological and deterministic whatever the display order or the filters: the window runs over every row
       -- of the journal (a filter on the list never changes a row's balance), oldest first, ties by row id.
       SUM(rows.balance_amount) OVER (PARTITION BY rows.journal_id
                                      ORDER BY COALESCE(rows.bank_date, rows.date), rows.id
                                      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_balance
  FROM rows
)""")

    def _nc_source_record(self):
        """The authoritative record behind the row, from its real links (never from its label): a transfer side opens
        its transfer, a payment its payment, a bank transaction / confirmation its statement line, else the entry."""
        self.ensure_one()
        if self.operation in ('settlement', 'bank') and self.statement_line_id:
            return self.statement_line_id
        if self.payment_id.nc_transfer_id:
            return self.payment_id.nc_transfer_id
        return self.payment_id or self.statement_line_id or self.move_id

    def action_open_source(self):
        """The row action (the whole row is clickable). Read access is checked as the user before anything opens."""
        record = self._nc_source_record()
        record.check_access('read')
        return {'type': 'ir.actions.act_window', 'res_model': record._name, 'res_id': record.id,
                'view_mode': 'form', 'views': [(False, 'form')], 'target': 'current'}
