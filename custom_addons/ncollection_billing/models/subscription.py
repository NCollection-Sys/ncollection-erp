# -*- coding: utf-8 -*-
"""Subscription billing engine (P2-T11).

Generates exactly one correct account.move invoice on purchase (activate) and
on each renewal, applies UAE VAT 5%, prorates mid-cycle upgrades, and tracks
payment status back onto the subscription. Uses Odoo's accounting engine
(FINANCIAL_PLATFORM_ARCHITECTURE §4/§5) — never a custom invoice model.
"""
from odoo import api, fields, models


class Subscription(models.Model):
    _inherit = 'ncollection.subscription'

    invoice_ids = fields.One2many('account.move', 'nc_subscription_id', string='Invoices')
    invoice_count = fields.Integer(compute='_compute_invoice_count')
    payment_status = fields.Selection(
        selection=[
            ('no_invoice', 'No Invoice'),
            ('invoiced', 'Invoiced'),
            ('paid', 'Paid'),
            ('overdue', 'Overdue'),
        ],
        compute='_compute_payment_status', store=True, default='no_invoice', tracking=True,
    )

    def _compute_invoice_count(self):
        for sub in self:
            sub.invoice_count = len(sub.invoice_ids)

    @api.depends('invoice_ids.payment_state', 'invoice_ids.state', 'invoice_ids.invoice_date_due')
    def _compute_payment_status(self):
        today = fields.Date.context_today(self)
        for sub in self:
            posted = sub.invoice_ids.filtered(
                lambda m: m.state == 'posted' and m.move_type == 'out_invoice')
            if not posted:
                sub.payment_status = 'no_invoice'
            elif all(m.payment_state in ('paid', 'in_payment', 'reversed') for m in posted):
                sub.payment_status = 'paid'
            elif any(m.payment_state == 'not_paid' and m.invoice_date_due
                     and m.invoice_date_due < today for m in posted):
                sub.payment_status = 'overdue'
            else:
                sub.payment_status = 'invoiced'

    # ---- lifecycle hooks -> invoicing ------------------------------------

    def action_activate(self):
        res = super().action_activate()
        for sub in self:
            sub._nc_bill_period('purchase', sub.start_date, sub.end_date)
        return res

    def action_renew(self):
        # Capture each subscription's end_date BEFORE super() extends it: the
        # renewed period runs from the old end to the new end, giving the
        # renewal invoice a distinct period key from the purchase invoice.
        old_ends = {s.id: s.end_date for s in self}
        res = super().action_renew()
        today = fields.Date.context_today(self)
        for sub in self:
            period_start = old_ends.get(sub.id) or today
            sub._nc_bill_period('renewal', period_start, sub.end_date)
        return res

    def write(self, vals):
        old_plans = {s.id: s.plan_id for s in self} if 'plan_id' in vals else {}
        res = super().write(vals)
        if 'plan_id' in vals:
            for sub in self:
                old = old_plans.get(sub.id)
                if old and old != sub.plan_id and sub.status == 'active':
                    sub._nc_proration_invoice(old, sub.plan_id)
        return res

    # ---- billing internals -----------------------------------------------

    def _nc_period_amount(self, plan=None):
        """Plan price for this subscription's billing cycle."""
        self.ensure_one()
        plan = plan or self.plan_id
        return plan.yearly_price if self.billing_cycle == 'yearly' else plan.monthly_price

    def _nc_bill_period(self, reason, period_start, period_end):
        """Create the single invoice for this billing period (idempotent per
        period_start — the 'exactly one invoice' acceptance)."""
        self.ensure_one()
        if not self.plan_id:
            return self.env['account.move']
        existing = self.invoice_ids.filtered(
            lambda m: m.move_type == 'out_invoice' and m.nc_period_start == period_start)
        if existing:
            return existing[:1]
        amount = self._nc_period_amount()
        return self._nc_create_invoice(amount, period_start, period_end, reason)

    def _nc_proration_invoice(self, old_plan, new_plan):
        """Invoice the prorated price difference for a mid-cycle UPGRADE."""
        self.ensure_one()
        today = fields.Date.context_today(self)
        if not self.end_date or self.end_date <= today:
            return self.env['account.move']
        cycle_start = self.start_date or today
        total_days = (self.end_date - cycle_start).days or 1
        remaining = (self.end_date - today).days
        diff = self._nc_period_amount(new_plan) - self._nc_period_amount(old_plan)
        prorated = diff * remaining / total_days
        if prorated <= 0:  # only charge upgrades; downgrades adjust at renewal
            return self.env['account.move']
        return self._nc_create_invoice(prorated, today, self.end_date, 'proration')

    def _nc_create_invoice(self, amount, period_start, period_end, reason):
        """Build + post one customer invoice via Odoo's accounting engine."""
        self.ensure_one()
        company = self.env.company
        partner = self.tenant_id._nc_ensure_partner()
        product = company._nc_billing_product()
        tax = company._nc_billing_tax()
        move = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': partner.id,
            'invoice_date': fields.Date.context_today(self),
            'currency_id': company.currency_id.id,
            'nc_subscription_id': self.id,
            'nc_tenant_id': self.tenant_id.id,
            'nc_period_start': period_start,
            'nc_period_end': period_end,
            'invoice_origin': self.name,
            'invoice_line_ids': [(0, 0, {
                'product_id': product.id,
                'name': '%s — %s (%s → %s)' % (
                    self.plan_id.name, reason, period_start, period_end),
                'quantity': 1.0,
                'price_unit': amount,
                'tax_ids': [(6, 0, tax.ids)],
            })],
        })
        move.action_post()
        return move

    def action_view_invoices(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Invoices',
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'domain': [('nc_subscription_id', '=', self.id)],
            'context': {'create': False},
        }
