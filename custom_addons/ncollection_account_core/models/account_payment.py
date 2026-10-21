# -*- coding: utf-8 -*-
"""Payment: Files / View / Print Receipt, the partner badges, and allocation to open invoices / bills.

ALLOCATION. A payment entered on the payment form may name ONE invoice / bill to apply first (optional). On
Confirm it is posted by Odoo itself (``action_post``), and its open receivable / payable journal item is then applied,
document after document, through ``account.move.js_assign_outstanding_line`` -- the very method behind the
"Outstanding credits / debits" list on an invoice, i.e. Odoo's own ``reconcile()``. So the accounting relationship IS
the native partial reconciliation (amounts, currencies, exchange differences, partial / full states); nothing here
stores "this payment paid that invoice" on its own. Whatever is left after the eligible documents are settled stays
on the partner's account as genuine outstanding credit, exactly as Odoo leaves an unapplied payment.

The order is deterministic: the selected document first, then the others by the earliest due date of their open
installments, then by document date, then id. The documents eligible are the ones Odoo's own outstanding widget
would offer this payment's journal item to: posted, same receivable / payable account, same commercial partner,
same company (or its branches), not reconciled, with an open amount on the other side (an invoice for a customer
receipt, a credit note for a refund to the customer, a bill for a vendor payment, a vendor credit note for a refund
from the vendor). Journal entries that are not invoices / bills / credit notes / receipts are never targeted.

The preview shown before Confirm is computed from the same list and the same residuals, and changes nothing. A
payment posted any other way (the Register Payment wizard of an invoice, a list's mass Confirm, an online payment,
a bank reconciliation) is NOT allocated here: those flows reconcile what they were asked to. The same goes for the
list's mass "Confirm", which is Odoo's own ``action_post``; only the form's Confirm button allocates.
"""
from markupsafe import Markup, escape

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools.misc import formatLang

_PAY_TERM_TYPES = ('asset_receivable', 'liability_payable')
_DOCUMENT_TYPES = {
    'customer': ('out_invoice', 'out_refund', 'out_receipt'),
    'supplier': ('in_invoice', 'in_refund', 'in_receipt'),
}


class AccountPayment(models.Model):
    _inherit = ['account.payment', 'nc.partner.roles.mixin', 'nc.attachment.mixin', 'nc.document.print.mixin']
    _name = 'account.payment'

    nc_apply_first_move_id = fields.Many2one(
        'account.move', string='Apply First To', copy=False, check_company=True,
        domain="[('id', 'in', nc_eligible_move_ids)]",
        help="Optional. The invoice / bill this payment settles first; any remainder goes to the partner's other open "
             "documents, earliest due first, and what is still left stays as an unapplied credit.")
    nc_eligible_move_ids = fields.Many2many(
        'account.move', string='Open Documents', compute='_compute_nc_eligible_move_ids',
        help="The partner's posted invoices / bills / credit notes this payment can be applied to.")
    nc_allocation_html = fields.Html(
        string='Allocation', compute='_compute_nc_allocation', sanitize=False,
        help="Before Confirm: how the payment will be applied. After: how it was applied (from the reconciliation).")
    nc_unallocated_amount = fields.Monetary(
        string='Unallocated', compute='_compute_nc_unallocated_amount', currency_field='currency_id',
        help="The part of the payment not applied to any document (an outstanding credit on the partner's account).")

    # ------------------------------------------------------------------ eligibility and order (ONE place)
    def _nc_open_document_lines(self):
        """The open receivable / payable journal items this payment can be applied to (read as the user)."""
        self.ensure_one()
        Line = self.env['account.move.line']
        account = self.destination_account_id
        if (not self.partner_id or self.partner_type not in _DOCUMENT_TYPES or not account
                or account.account_type not in _PAY_TERM_TYPES):
            return Line
        return Line.search([
            ('account_id', '=', account.id),
            ('parent_state', '=', 'posted'),
            *Line._check_company_domain(self.company_id),
            ('partner_id', '=', self.partner_id.commercial_partner_id.id),
            ('reconciled', '=', False),
            # a receipt (inbound) settles debits (invoices); a refund paid out (outbound) settles credits
            ('balance', '>' if self.payment_type == 'inbound' else '<', 0.0),
            ('move_id.move_type', 'in', _DOCUMENT_TYPES[self.partner_type]),
            '|', ('amount_residual', '!=', 0.0), ('amount_residual_currency', '!=', 0.0),
        ])

    def _nc_allocation_targets(self):
        """``[(document, its open lines)]`` in allocation order: the selected one first, then earliest due."""
        self.ensure_one()
        lines = self._nc_open_document_lines()
        by_move = {}
        for line in lines:
            by_move.setdefault(line.move_id, self.env['account.move.line'])
            by_move[line.move_id] |= line

        def order(move):
            open_lines = by_move[move]
            due = min(line.date_maturity or move.invoice_date_due or move.date for line in open_lines)
            return (due, move.invoice_date or move.date, move.id)

        first = self.nc_apply_first_move_id
        moves = sorted((m for m in by_move if m != first), key=order)
        if first in by_move:
            moves.insert(0, first)
        return [(move, by_move[move]) for move in moves]

    def _nc_open_in_payment_currency(self, lines):
        """What is open on ``lines`` (one document's), in this payment's currency."""
        if all(line.currency_id == self.currency_id for line in lines):
            return abs(sum(lines.mapped('amount_residual_currency')))
        return self.company_id.currency_id._convert(
            abs(sum(lines.mapped('amount_residual'))), self.currency_id, self.company_id,
            self.date or fields.Date.context_today(self))

    @api.depends('partner_id', 'partner_type', 'payment_type', 'destination_account_id', 'company_id')
    def _compute_nc_eligible_move_ids(self):
        for payment in self:
            payment.nc_eligible_move_ids = payment._nc_open_document_lines().move_id

    # ------------------------------------------------------------------ the plan, and the actual allocation
    def _nc_allocation_plan(self):
        """``(rows, unallocated)``: each row ``{'move', 'due', 'applied'}``, amounts in the payment's currency.
        Pure: reads residuals, writes nothing."""
        self.ensure_one()
        currency = self.currency_id
        remaining = self.amount
        rows = []
        for move, lines in self._nc_allocation_targets():
            if currency.compare_amounts(remaining, 0.0) <= 0:
                break
            due = currency.round(self._nc_open_in_payment_currency(lines))
            applied = min(remaining, due)
            if currency.is_zero(applied):
                continue
            rows.append({'move': move, 'due': due, 'applied': applied})
            remaining = currency.round(remaining - applied)
        return rows, max(remaining, 0.0)

    def _nc_counterpart_lines(self):
        self.ensure_one()
        return self.move_id.line_ids.filtered(lambda line: line.account_id.account_type in _PAY_TERM_TYPES)

    def _nc_applied_rows(self):
        """``(rows, unallocated)`` of a posted payment, read from its partial reconciliations."""
        self.ensure_one()
        rows = {}
        counterparts = self._nc_counterpart_lines()
        for line in counterparts:
            credit_side = line.balance < 0
            partials = line.matched_debit_ids if credit_side else line.matched_credit_ids
            for partial in partials:
                other = partial.debit_move_id if credit_side else partial.credit_move_id
                amount = partial.credit_amount_currency if credit_side else partial.debit_amount_currency
                row = rows.setdefault(other.move_id, {'move': other.move_id, 'due': None, 'applied': 0.0})
                row['applied'] += amount
        unallocated = abs(sum(counterparts.filtered(lambda line: not line.reconciled).mapped(
            'amount_residual_currency')))
        return list(rows.values()), unallocated

    @api.depends('state', 'move_id.line_ids.amount_residual', 'move_id.line_ids.amount_residual_currency')
    def _compute_nc_unallocated_amount(self):
        """The list column: what a POSTED payment still has unapplied, from its own journal item (no search, no plan).
        A draft payment has not been applied to anything yet, so it shows nothing here (its plan is on the form)."""
        for payment in self:
            payment.nc_unallocated_amount = (
                abs(sum(payment._nc_counterpart_lines().filtered(lambda line: not line.reconciled).mapped(
                    'amount_residual_currency'))) if payment.move_id and payment.state != 'draft' else 0.0)

    @api.depends('state', 'amount', 'currency_id', 'date', 'partner_id', 'partner_type', 'payment_type',
                 'destination_account_id', 'company_id', 'nc_apply_first_move_id', 'move_id.line_ids.amount_residual')
    def _compute_nc_allocation(self):
        """The form's table: the PLAN before Confirm, what was applied after. (The preview converts at the payment
        date; the reconciliation itself uses Odoo's own rates, so a multi-currency preview is an estimate.)"""
        for payment in self:
            if payment.state == 'draft' or not payment.move_id:
                rows, unallocated = payment._nc_allocation_plan()
                planned = True
            else:
                rows, unallocated = payment._nc_applied_rows()
                planned = False
            payment.nc_allocation_html = payment._nc_allocation_table(rows, unallocated, planned)

    def _nc_allocation_table(self, rows, unallocated, planned):
        self.ensure_one()
        if not self.partner_id:
            return False

        def money(amount):
            return escape(formatLang(self.env, amount, currency_obj=self.currency_id))

        head = Markup('<table class="table table-sm o_nc_allocation mb-0"><thead><tr><th>%s</th>%s'
                      '<th class="text-end">%s</th></tr></thead><tbody>') % (
            self.env._("Document"),
            Markup('<th class="text-end">%s</th>') % self.env._("Due") if planned else Markup(''),
            self.env._("To apply") if planned else self.env._("Applied"))
        body = Markup('')
        for row in rows:
            body += Markup('<tr><td dir="auto">%s</td>%s<td class="text-end">%s</td></tr>') % (
                row['move'].display_name,
                Markup('<td class="text-end">%s</td>') % money(row['due']) if planned else Markup(''),
                money(row['applied']))
        if not rows:
            body += Markup('<tr><td colspan="3" class="text-muted">%s</td></tr>') % self.env._(
                "No open document to apply to.")
        foot = Markup('<tr class="fw-bold"><td %s>%s</td><td class="text-end">%s</td></tr></tbody></table>') % (
            Markup('colspan="2"') if planned else Markup(''), self.env._("Unallocated"), money(unallocated))
        table = head + body + foot
        if planned and self._nc_plan_is_an_estimate(rows):
            table += Markup('<p class="text-muted small mt-2 mb-0 o_nc_allocation_estimate">%s</p>') % self.env._(
                "Estimate: some of these documents are in a different currency from this payment. The amounts "
                "above are converted at the payment date; the final amounts, and any exchange difference, are "
                "decided by the accounting when the payment is confirmed and reconciled.")
        return table

    def _nc_plan_is_an_estimate(self, rows):
        """A preview is exact only when every document it lists is in the payment's own currency: there is no
        conversion, so nothing for the reconciliation to decide differently. With any other currency it is an estimate:
        Odoo's reconciliation (its rates, its exchange differences) is authoritative, never these figures."""
        self.ensure_one()
        return any(row['move'].currency_id != self.currency_id for row in rows)

    # ------------------------------------------------------------------ Confirm = post, then apply
    def _nc_check_apply_first(self):
        for payment in self:
            first = payment.nc_apply_first_move_id
            if first and first not in payment._nc_open_document_lines().move_id:
                raise UserError(self.env._(
                    "%(document)s is not an open document of %(partner)s that this payment can be applied to.",
                    document=first.display_name, partner=payment.partner_id.display_name))

    def action_nc_post_and_allocate(self):
        """The payment form's Confirm: Odoo posts the payment, then it is applied through Odoo's reconciliation."""
        self._nc_check_apply_first()
        self.action_post()
        self._nc_apply_allocation()
        return True

    def _nc_apply_allocation(self):
        for payment in self:
            for move, _lines in payment._nc_allocation_targets():
                open_lines = payment._nc_counterpart_lines().filtered(lambda line: not line.reconciled)
                if not open_lines:
                    break
                for line in open_lines:
                    if not line.reconciled:
                        move.js_assign_outstanding_line(line.id)
                move.matched_payment_ids |= payment            # the link the Register Payment wizard also keeps

    # ------------------------------------------------------------------ View / Print Receipt
    def _nc_print_report(self):
        return self.env.ref('account.action_report_payment_receipt')

    # ------------------------------------------------------------------ the printed receipt
    def _nc_amount_in_words(self):
        """The payment amount in words, in the language the receipt is printed in; empty when that is not available (an
        unsupported language or currency) rather than failing the whole receipt."""
        self.ensure_one()
        try:
            lang = 'ar' if (self.env.lang or '').startswith('ar') else 'en'
            return self.env['ncollection.amount.words'].convert(self.amount, self.currency_id, lang=lang)
        except Exception:                                              # noqa: BLE001 - words are a courtesy line
            return ''

    def _nc_receipt_labels(self):
        """The words a receipt uses for the two sides of this payment, from its own direction (never guessed from the
        partner's name): who the money came from / went to, and what the applied documents are called."""
        self.ensure_one()
        incoming = self.payment_type == 'inbound'
        customer = self.partner_type == 'customer'
        if incoming:
            party = self.env._("Received from") if customer else self.env._("Refund received from")
        else:
            party = self.env._("Paid to") if not customer else self.env._("Refunded to")
        document = self.env._("Invoice") if customer else self.env._("Bill")
        return {'party': party, 'document': document}
