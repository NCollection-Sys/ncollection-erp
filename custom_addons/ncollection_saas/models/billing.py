# -*- coding: utf-8 -*-
"""Subscription billing engine (P2-T11, admin DB).

Turns subscription lifecycle events into invoices on the PLATFORM company:
- activation and each renewal produce exactly one invoice for the covered
  period (plan price for the cycle, + UAE VAT 5%),
- a mid-cycle upgrade produces one prorated invoice for the price difference
  over the remaining days of the running period,
- the subscription tracks the aggregate payment status of its invoices.

Idempotency is enforced at the database by account.move's unique
(subscription, period_key) index (see account_move.py): a repeated lifecycle
call finds the existing invoice and returns it instead of creating a duplicate,
so "activate/renew always yields exactly one correct invoice" holds even under
retries. These are the platform's OWN admin-DB invoices, not tenant-DB access
(two-layer separation — DELIVERABLE_1 §2.5).
"""

import logging

from dateutil.relativedelta import relativedelta

from odoo import Command, api, fields, models

from ..hooks import ensure_billing_setup

_logger = logging.getLogger(__name__)


class SubscriptionBilling(models.Model):
    _inherit = 'ncollection.subscription'

    invoice_ids = fields.One2many(
        'account.move', 'ncollection_subscription_id', string='Invoices')
    invoice_count = fields.Integer(compute='_compute_invoice_count', string='Invoices')
    invoice_payment_state = fields.Selection(
        selection=[
            ('none', 'Nothing to Pay'),
            ('not_paid', 'Not Paid'),
            ('partial', 'Partially Paid'),
            ('paid', 'Paid'),
        ],
        compute='_compute_invoice_payment_state',
        store=True,
        string='Payment Status',
        help='Aggregate payment status of this subscription\'s posted invoices.',
    )

    @api.depends('invoice_ids')
    def _compute_invoice_count(self):
        for sub in self:
            sub.invoice_count = len(sub.invoice_ids)

    @api.depends('invoice_ids.payment_state', 'invoice_ids.state', 'invoice_ids.move_type')
    def _compute_invoice_payment_state(self):
        settled = ('paid', 'in_payment', 'reversed')
        for sub in self:
            posted = sub.invoice_ids.filtered(
                lambda m: m.state == 'posted' and m.move_type == 'out_invoice')
            if not posted:
                sub.invoice_payment_state = 'none'
            elif all(m.payment_state in settled for m in posted):
                sub.invoice_payment_state = 'paid'
            elif any(m.payment_state in settled + ('partial',) for m in posted):
                sub.invoice_payment_state = 'partial'
            else:
                sub.invoice_payment_state = 'not_paid'

    # ------------------------------------------------------------------
    # Billing primitives
    # ------------------------------------------------------------------
    def _billing_price(self, plan=None, cycle=None):
        """Plan price for the given billing cycle (defaults to this sub's)."""
        self.ensure_one()
        plan = plan or self.plan_id
        cycle = cycle or self.billing_cycle
        return plan.yearly_price if cycle == 'yearly' else plan.monthly_price

    def _billing_partner(self):
        """Return (creating once) the res.partner this tenant is billed as.

        The customer record lives in the ADMIN DB — it represents the tenant as
        the platform's paying customer, never a tenant-DB contact.
        """
        self.ensure_one()
        tenant = self.tenant_id
        if tenant.partner_id:
            return tenant.partner_id
        partner = self.env['res.partner'].create({  # arch-guard: admin-db-billing
            'name': tenant.company_name or self.name,
            'company_type': 'company',
            'email': tenant.email or False,
            'is_company': True,
        })
        tenant.partner_id = partner
        return partner

    def _create_subscription_invoice(self, period_key, amount=None, label=None, plan=None):
        """Create + post one posted invoice for `period_key`, idempotently.

        The unique (subscription, period_key) index means a second call for the
        same key returns the already-existing invoice instead of duplicating —
        the P2-T11 "exactly one invoice" guarantee, retry-safe.
        """
        self.ensure_one()
        if not period_key:
            return self.env['account.move']  # arch-guard: admin-db-billing
        Move = self.env['account.move']  # arch-guard: admin-db-billing
        existing = Move.search([
            ('ncollection_subscription_id', '=', self.id),
            ('ncollection_period_key', '=', period_key),
        ], limit=1)
        if existing:
            return existing
        tax, product = ensure_billing_setup(self.env)
        plan = plan or self.plan_id
        amount = self._billing_price(plan) if amount is None else amount
        partner = self._billing_partner()
        label = label or self.env._(
            '%(plan)s subscription (%(cycle)s) — %(name)s',
            plan=plan.name, cycle=self.billing_cycle, name=self.name)
        move = Move.create({  # arch-guard: admin-db-billing
            'move_type': 'out_invoice',
            'partner_id': partner.id,
            'ncollection_subscription_id': self.id,
            'ncollection_tenant_id': self.tenant_id.id,
            'ncollection_period_key': period_key,
            'invoice_line_ids': [Command.create({
                'product_id': product.id,
                'name': label,
                'quantity': 1,
                'price_unit': amount,
                'tax_ids': [Command.set(tax.ids)],
            })],
        })
        move.action_post()
        _logger.info(
            "Billed subscription %s period '%s': invoice %s (%.2f + 5%% VAT)",
            self.name, period_key, move.name, amount)
        return move

    # ------------------------------------------------------------------
    # Lifecycle billing hooks (called from subscription.py)
    # ------------------------------------------------------------------
    def _bill_on_activation(self):
        """First-period invoice. Sets end_date to one cycle out if it is unset,
        then bills the period ending there."""
        self.ensure_one()
        if not self.end_date:
            delta = (relativedelta(years=1) if self.billing_cycle == 'yearly'
                     else relativedelta(months=1))
            self.end_date = self.start_date + delta
        return self._create_subscription_invoice(period_key=str(self.end_date))

    def _create_proration_invoice(self, old_plan):
        """Charge the price difference of a mid-cycle upgrade, prorated over the
        days left in the running period. Downgrades raise no immediate invoice
        (any credit is applied at the next renewal — not P2-T11 scope)."""
        self.ensure_one()
        if not self.end_date:
            return self.env['account.move']  # arch-guard: admin-db-billing
        today = fields.Date.context_today(self)
        if self.end_date <= today:
            return self.env['account.move']  # arch-guard: admin-db-billing
        diff = self._billing_price(self.plan_id) - self._billing_price(old_plan)
        if diff <= 0:
            return self.env['account.move']  # arch-guard: admin-db-billing
        delta = (relativedelta(years=1) if self.billing_cycle == 'yearly'
                 else relativedelta(months=1))
        period_start = self.end_date - delta
        cycle_days = (self.end_date - period_start).days or 1
        remaining = (self.end_date - today).days
        frac = max(0.0, min(1.0, remaining / cycle_days))
        amount = round(diff * frac, 2)
        if amount <= 0:
            return self.env['account.move']  # arch-guard: admin-db-billing
        period_key = 'upgrade:%s:%s->%s' % (today, old_plan.code, self.plan_id.code)
        label = self.env._(
            'Upgrade proration %(old)s → %(new)s (%(days)s days) — %(name)s',
            old=old_plan.name, new=self.plan_id.name, days=remaining, name=self.name)
        return self._create_subscription_invoice(
            period_key=period_key, amount=amount, label=label)

    def action_renew(self):
        """Extend the period (base behaviour), then bill the new period once."""
        res = super().action_renew()
        for sub in self:
            if sub.status == 'active' and sub.end_date:
                sub._create_subscription_invoice(period_key=str(sub.end_date))
        return res
