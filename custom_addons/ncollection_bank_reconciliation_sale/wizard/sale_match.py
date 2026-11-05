"""The Sales action of the bank matching workspace.

A sales order is NOT a receivable: nothing here reconciles a bank transaction with an order or books revenue because a
transaction mentions one. The workflow is Odoo's own: find the customer's confirmed orders, create the invoice with
the native invoicing (``sale.order._create_invoices`` for a regular invoice, ``sale.advance.payment.inv`` for a down
payment), post it (a separate, explicit choice), and add the posted invoice's receivable item to the transaction, where
the normal reconciliation validation applies (partial amounts, exchange differences, undo).

Every call runs as the user: invoicing needs the invoicing rights the native layer checks, a down payment needs the
Sales right the native wizard checks, and a refusal is shown as such. No sudo.
"""
from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError


class SaleMatch(models.TransientModel):
    _name = 'ncollection.bank.sale.match'
    _description = 'Bank Matching: Sales Orders'

    statement_line_id = fields.Many2one('account.bank.statement.line', required=True, readonly=True)
    partner_id = fields.Many2one(related='statement_line_id.partner_id')
    company_id = fields.Many2one(related='statement_line_id.company_id')
    currency_id = fields.Many2one(related='statement_line_id.currency_id')
    transaction_amount = fields.Monetary(related='statement_line_id.amount', currency_field='currency_id')
    line_ids = fields.One2many('ncollection.bank.sale.match.line', 'wizard_id')
    method = fields.Selection(
        [('delivered', 'Regular invoice'), ('percentage', 'Down payment (percentage)'),
         ('fixed', 'Down payment (fixed amount)')], default='delivered', required=True, string='Invoice')
    percentage = fields.Float(string='Down payment (%)', default=50.0)
    fixed_amount = fields.Monetary(string='Down payment amount', currency_field='order_currency_id')
    order_currency_id = fields.Many2one('res.currency', compute='_compute_order_currency')
    post = fields.Boolean(
        string='Post the invoice and add it to the transaction', default=False,
        help='Off: the invoice stays a draft for review. On: it is posted (normal Odoo posting) and its receivable '
             'item is added to this transaction, which you still validate.')

    @api.depends('line_ids.selected')
    def _compute_order_currency(self):
        for wizard in self:
            orders = wizard.line_ids.filtered('selected').order_id
            wizard.order_currency_id = orders[:1].currency_id or wizard.currency_id

    # ----------------------------------------------------------- candidates
    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ctx = self.env.context
        line_id = ctx.get('default_statement_line_id') or (
            ctx.get('active_model') == 'account.bank.statement.line' and ctx.get('active_id'))
        if not line_id:
            return res
        st_line = self.env['account.bank.statement.line'].browse(line_id)
        res['statement_line_id'] = st_line.id
        if 'line_ids' in fields_list:
            res['line_ids'] = [(0, 0, vals) for vals in self._candidate_values(st_line)]
        return res

    @api.model
    def _candidate_orders(self, st_line):
        """Confirmed orders of the transaction's customer, in the transaction's company. Cancelled and draft orders
        are not offered; the record rules already stop another company's orders, the domain says it as well."""
        if not st_line.partner_id:
            return self.env['sale.order']
        return self.env['sale.order'].search([
            ('company_id', '=', st_line.company_id.id),
            ('state', '=', 'sale'),
            ('partner_id', 'child_of', st_line.partner_id.commercial_partner_id.id),
        ], order='date_order desc, id desc', limit=50)

    @api.model
    def _candidate_values(self, st_line):
        reference = (st_line.payment_ref or '').lower()
        orders = self._candidate_orders(st_line)
        mentioned = orders.filtered(lambda o: o.name and o.name.lower() in reference)
        return [{
            'order_id': order.id,
            # an order named in the bank reference is ticked for the user; the rest are the user's choice
            'selected': order in mentioned and order.invoice_status == 'to invoice',
        } for order in orders]

    # -------------------------------------------------------------- actions
    def _selected_orders(self):
        orders = self.line_ids.filtered('selected').order_id
        if not orders:
            raise UserError(self.env._("Tick at least one sales order."))
        return orders

    def _check_transaction(self):
        st_line = self.statement_line_id
        st_line.check_access('write')
        if st_line.is_reconciled:
            raise UserError(self.env._(
                "The bank transaction %(name)s is already reconciled.", name=st_line.display_name))
        if not st_line.partner_id:
            raise UserError(self.env._("Set the customer on the transaction first."))
        return st_line

    def _check_orders(self, orders):
        st_line = self._check_transaction()
        commercial = st_line.partner_id.commercial_partner_id
        # re-read under lock: a second user (or a double click) must not create the same invoice twice
        st_line._nc_lock_rows('sale_order', orders.ids)
        orders.invalidate_recordset(['invoice_status', 'invoice_ids', 'state'])
        for order in orders:
            if order.company_id != st_line.company_id or order.partner_id.commercial_partner_id != commercial:
                raise UserError(self.env._(
                    "%(order)s belongs to another customer or company than the transaction.", order=order.name))
            if order.state != 'sale':
                raise UserError(self.env._("%(order)s is not a confirmed sales order.", order=order.name))
            drafts = order.invoice_ids.filtered(lambda m: m.state == 'draft' and m.move_type == 'out_invoice')
            if drafts:
                raise UserError(self.env._(
                    "%(order)s already has a draft invoice (%(invoices)s). Post or cancel it first: a second "
                    "invoice would bill the same lines twice.", order=order.name,
                    invoices=', '.join(drafts.mapped(lambda m: m.name or str(m.id)))))
            if order.invoice_status != 'to invoice':
                raise UserError(self.env._(
                    "%(order)s has nothing left to invoice (it is already invoiced, or nothing is ready to invoice).",
                    order=order.name))

    def action_create_invoice(self):
        """Create the invoice(s) with Odoo's own invoicing; optionally post and add to the transaction."""
        self.ensure_one()
        orders = self._selected_orders()
        self._check_orders(orders)
        if not self.env['account.move'].has_access('create'):
            raise UserError(self.env._("You do not have the right to create invoices."))
        if self.method == 'delivered':
            invoices = orders._create_invoices(final=True)
        else:
            if len(orders) != 1:
                raise UserError(self.env._("A down payment is created for one sales order at a time."))
            try:
                advance = self.env['sale.advance.payment.inv'].with_context(
                    active_model='sale.order', active_ids=orders.ids).create({
                        'advance_payment_method': self.method,
                        'amount': self.percentage,
                        'fixed_amount': self.fixed_amount,
                        'sale_order_ids': [(6, 0, orders.ids)],
                    })
            except AccessError:
                raise UserError(self.env._(
                    "Creating a down payment needs the Sales user right as well; ask a sales user or an "
                    "administrator.")) from None
            advance._check_amount_is_positive()
            invoices = advance._create_invoices(orders)
        if not invoices:
            raise UserError(self.env._("Nothing could be invoiced for the selected orders."))
        st_line = self.statement_line_id
        for invoice in invoices:
            invoice.message_post(body=self.env._(
                "Created from the bank matching workspace for the bank transaction %(name)s by %(user)s.",
                name=st_line.display_name, user=self.env.user.name))
        if self.post:
            invoices.action_post()
            items = invoices.line_ids.filtered(
                lambda ml: ml.account_id.account_type == 'asset_receivable' and not ml.reconciled)
            if items:
                st_line.add_multiple_lines([('id', 'in', items.ids)])
        return {'type': 'ir.actions.act_window_close'}

    def action_add_posted_invoices(self):
        """Orders already invoiced: add the receivable items of their POSTED, unpaid invoices to the transaction."""
        self.ensure_one()
        orders = self._selected_orders()
        st_line = self._check_transaction()
        invoices = orders.invoice_ids.filtered(
            lambda m: m.state == 'posted' and m.move_type == 'out_invoice' and m.company_id == st_line.company_id)
        items = invoices.line_ids.filtered(
            lambda ml: ml.account_id.account_type == 'asset_receivable' and not ml.reconciled)
        if not items:
            raise UserError(self.env._("The selected orders have no posted, unpaid invoice to match."))
        st_line.add_multiple_lines([('id', 'in', items.ids)])
        return {'type': 'ir.actions.act_window_close'}


class SaleMatchLine(models.TransientModel):
    _name = 'ncollection.bank.sale.match.line'
    _description = 'Bank Matching: Sales Order Candidate'

    wizard_id = fields.Many2one('ncollection.bank.sale.match', required=True, ondelete='cascade')
    order_id = fields.Many2one('sale.order', required=True, readonly=True)
    selected = fields.Boolean()
    date_order = fields.Datetime(related='order_id.date_order')
    partner_id = fields.Many2one(related='order_id.partner_id')
    currency_id = fields.Many2one(related='order_id.currency_id')
    amount_total = fields.Monetary(related='order_id.amount_total')
    amount_invoiced = fields.Monetary(related='order_id.amount_invoiced')
    amount_to_invoice = fields.Monetary(related='order_id.amount_to_invoice')
    invoice_status = fields.Selection(related='order_id.invoice_status')
    other_currency = fields.Boolean(compute='_compute_other_currency')

    @api.depends('currency_id', 'wizard_id.currency_id')
    def _compute_other_currency(self):
        for line in self:
            line.other_currency = bool(line.currency_id and line.currency_id != line.wizard_id.currency_id)
