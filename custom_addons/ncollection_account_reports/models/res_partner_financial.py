# -*- coding: utf-8 -*-
"""Customers / Suppliers financial columns and the partner Summary (Requests 02 / 03).

ONE SOURCE OF TRUTH. The balance shown on the Customers / Suppliers list is the sum of the partner's POSTED journal
items on receivable (customers) or payable (suppliers) accounts of the companies the user is working in, in COMPANY
currency: exactly the closing balance of the Customer / Supplier Statement (same engine, ``partner.ledger``) run up to
today with no date filter. Clicking it, or Summary, opens that statement for the partner, so the two always agree.

STATUS is a classification of that balance and of the partner's open documents, never of the balance alone:
  * No Activity    - no posted item on the scoped accounts at all (a new partner is NOT "Paid");
  * Settled        - items exist and the balance is zero;
  * Outstanding    - the partner owes (customer) / is owed (supplier), and no open document is part-paid;
  * Partially Paid - an amount is still due and at least one open invoice / bill is partly paid;
  * Credit Balance - the sign is reversed: an overpayment or unapplied credit, shown apart from any debt.
Different currencies are never summed: the figure is company currency, and the column says so.
"""
from odoo import api, fields, models

RECEIVABLE = 'asset_receivable'
PAYABLE = 'liability_payable'
STATUS = [('none', 'No Activity'), ('settled', 'Settled'), ('outstanding', 'Outstanding'),
          ('partial', 'Partially Paid'), ('credit', 'Credit Balance')]


class ResPartner(models.Model):
    _inherit = 'res.partner'

    nc_fin_currency_id = fields.Many2one('res.currency', string='Balance Currency', compute='_compute_nc_financials')
    nc_receivable_balance = fields.Monetary(
        string='Receivable', compute='_compute_nc_financials', currency_field='nc_fin_currency_id',
        groups='account.group_account_readonly',
        help="Posted receivable balance in company currency (positive: the customer owes).")
    nc_payable_balance = fields.Monetary(
        string='Payable', compute='_compute_nc_financials', currency_field='nc_fin_currency_id',
        groups='account.group_account_readonly',
        help="Posted payable balance in company currency (positive: owed to the supplier).")
    nc_customer_status = fields.Selection(STATUS, string='Customer Status', compute='_compute_nc_financials',
                                          groups='account.group_account_readonly')
    nc_supplier_status = fields.Selection(STATUS, string='Supplier Status', compute='_compute_nc_financials',
                                          groups='account.group_account_readonly')
    nc_open_invoice_count = fields.Integer(string='Open Invoices', compute='_compute_nc_financials',
                                           groups='account.group_account_readonly')
    nc_open_bill_count = fields.Integer(string='Open Bills', compute='_compute_nc_financials',
                                        groups='account.group_account_readonly')

    def _nc_fin_companies(self):
        return self.env.companies

    @api.depends_context('allowed_company_ids')
    def _compute_nc_financials(self):
        """Batched: two grouped queries for the whole page, never one per partner. As the user (record rules)."""
        companies = self._nc_fin_companies()
        currency = self.env.company.currency_id
        partners = self.filtered('id')
        # exactly the partner_id the statement engine filters on (one row of the list = one statement)
        commercial = {p.id: p.id for p in partners}
        ids = list(commercial.values())
        balances, counts = {}, {}
        if ids:
            for partner, account_type, balance, count in self.env['account.move.line']._read_group(
                    [('partner_id', 'in', ids), ('parent_state', '=', 'posted'),
                     ('company_id', 'in', companies.ids),
                     ('account_id.account_type', 'in', (RECEIVABLE, PAYABLE))],
                    groupby=['partner_id', 'account_id.account_type'], aggregates=['balance:sum', '__count']):
                key = (partner.id, account_type)
                balances[key] = balances.get(key, 0.0) + balance
                counts[key] = counts.get(key, 0) + count
            open_docs = {}
            for partner, move_type, state, count in self.env['account.move']._read_group(
                    [('partner_id', 'in', ids), ('state', '=', 'posted'),
                     ('company_id', 'in', companies.ids),
                     ('move_type', 'in', ('out_invoice', 'in_invoice')),
                     ('payment_state', 'in', ('not_paid', 'partial'))],
                    groupby=['partner_id', 'move_type', 'payment_state'], aggregates=['__count']):
                open_docs[(partner.id, move_type, state)] = count
        for partner in self:
            cid = commercial.get(partner.id)
            receivable = currency.round(balances.get((cid, RECEIVABLE), 0.0))
            payable = currency.round(-balances.get((cid, PAYABLE), 0.0))
            partner.nc_fin_currency_id = currency
            partner.nc_receivable_balance = receivable
            partner.nc_payable_balance = payable
            if not cid:
                partner.update({'nc_customer_status': 'none', 'nc_supplier_status': 'none',
                                'nc_open_invoice_count': 0, 'nc_open_bill_count': 0})
                continue
            inv_partial = open_docs.get((cid, 'out_invoice', 'partial'), 0)
            bill_partial = open_docs.get((cid, 'in_invoice', 'partial'), 0)
            partner.nc_open_invoice_count = inv_partial + open_docs.get((cid, 'out_invoice', 'not_paid'), 0)
            partner.nc_open_bill_count = bill_partial + open_docs.get((cid, 'in_invoice', 'not_paid'), 0)
            partner.nc_customer_status = self._nc_status(
                currency, receivable, counts.get((cid, RECEIVABLE), 0), inv_partial)
            partner.nc_supplier_status = self._nc_status(
                currency, payable, counts.get((cid, PAYABLE), 0), bill_partial)

    @api.model
    def _nc_status(self, currency, balance, items, partial_docs):
        if not items:
            return 'none'
        if currency.is_zero(balance):
            return 'settled'
        if balance < 0:
            return 'credit'
        return 'partial' if partial_docs else 'outstanding'

    # ------------------------------------------------------------------ Summary (list action + smart button)
    def action_nc_partner_summary(self):
        """The partner's Customer / Supplier Statement: the same engine the Reports menu runs, preselected for this
        partner, the current company and the right scope. A partner that is both opens the side it is viewed from
        (context ``nc_partner_scope``), else the side with activity, customers first."""
        self.ensure_one()
        partner = self
        partner.check_access('read')
        scope = self.env.context.get('nc_partner_scope')
        if scope not in ('receivable', 'payable'):
            scope = 'payable' if (partner.nc_customer_status == 'none'
                                  and partner.nc_supplier_status != 'none') else 'receivable'
        today = fields.Date.context_today(self)
        filters = {
            'company_id': self.env.company.id, 'partner_scope': scope, 'partner_ids': [partner.id],
            'date_from': str(self.env.company.compute_fiscalyear_dates(today)['date_from']), 'date_to': str(today),
            'target_move': 'posted',
        }
        return {
            'type': 'ir.actions.client', 'tag': 'ncollection_account_reports.report_viewer',
            'name': self.env._("Summary — %s", partner.display_name),
            'context': {'nc_report_model': 'ncollection.account.report.statement', 'nc_report_filters': filters},
        }
