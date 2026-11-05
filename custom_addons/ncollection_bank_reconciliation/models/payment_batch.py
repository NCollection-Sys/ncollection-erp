"""Payment batches: several posted payments the bank books as one transaction (a deposit slip, a payroll file, a
supplier run). Community has no batch model (``account_batch_payment`` is Enterprise; OCA has none on 19.0), so this is
the smallest native one: a named group of payments of ONE bank journal and ONE direction, nothing else.

The batch never posts or changes anything itself. Matching it adds its payments' outstanding journal items to a bank
transaction in the workspace; Odoo's ``reconcile()`` does the rest. A payment already reconciled with the bank cannot
be added to a batch, and a payment can be in one open batch only, so the same payment can never be matched twice.
"""
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class PaymentBatch(models.Model):
    _name = 'ncollection.bank.payment.batch'
    _description = 'Payment Batch'
    _inherit = ['mail.thread']
    _order = 'date desc, id desc'
    _check_company_auto = True

    name = fields.Char(required=True, copy=False, default=lambda self: self.env._('New'), tracking=True)
    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    journal_id = fields.Many2one('account.journal', required=True, check_company=True, tracking=True,
                                 domain="[('type', '=', 'bank'), ('company_id', '=', company_id)]")
    payment_type = fields.Selection([('inbound', 'Receipts'), ('outbound', 'Payments')], required=True,
                                    default='inbound', tracking=True)
    payment_ids = fields.Many2many(
        'account.payment', 'nc_bank_batch_payment_rel', 'batch_id', 'payment_id', string='Payments',
        check_company=True,
        domain="[('journal_id', '=', journal_id), ('payment_type', '=', payment_type), "
               "('state', 'in', ('in_process', 'paid'))]")
    currency_id = fields.Many2one('res.currency', compute='_compute_amount')
    amount = fields.Monetary(compute='_compute_amount', currency_field='currency_id')
    payment_count = fields.Integer(compute='_compute_amount')
    state = fields.Selection([('open', 'To Match'), ('matched', 'Matched')], compute='_compute_state', store=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', self.env._('New')) == self.env._('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('ncollection.bank.payment.batch') or '/'
        return super().create(vals_list)

    @api.depends('payment_ids', 'journal_id')
    def _compute_amount(self):
        for batch in self:
            batch.currency_id = batch.journal_id.currency_id or batch.company_id.currency_id
            batch.amount = sum(batch.payment_ids.mapped('amount_signed')) if batch.payment_ids else 0.0
            batch.payment_count = len(batch.payment_ids)

    def _nc_outstanding_items(self):
        """The payments' journal items still waiting for the bank: reconcilable, not the partner side, open."""
        self.ensure_one()
        items = self.payment_ids.move_id.line_ids.filtered(
            lambda ml: ml.account_id.reconcile and not ml.reconciled
            and ml.account_id.account_type not in ('asset_receivable', 'liability_payable')
            and ml.parent_state == 'posted')
        return items

    @api.depends('payment_ids.move_id.line_ids.reconciled')
    def _compute_state(self):
        for batch in self:
            batch.state = 'matched' if batch.payment_ids and not batch._nc_outstanding_items() else 'open'

    @api.constrains('payment_ids', 'journal_id', 'payment_type')
    def _check_payments(self):
        for batch in self:
            wrong = batch.payment_ids.filtered(
                lambda p: p.journal_id != batch.journal_id or p.payment_type != batch.payment_type
                or p.state not in ('in_process', 'paid'))
            if wrong:
                raise ValidationError(self.env._(
                    "A batch holds posted %(type)s of %(journal)s only: %(payments)s do not fit.",
                    type=dict(self._fields['payment_type'].selection)[batch.payment_type],
                    journal=batch.journal_id.display_name, payments=', '.join(wrong.mapped('name'))))
            other = self.search([('id', '!=', batch.id), ('state', '=', 'open'),
                                ('payment_ids', 'in', batch.payment_ids.ids)])
            if other:
                raise ValidationError(self.env._(
                    "A payment can be in one open batch only; already in %(batches)s.",
                    batches=', '.join(other.mapped('name'))))
