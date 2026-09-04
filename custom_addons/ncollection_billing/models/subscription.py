# -*- coding: utf-8 -*-
"""Subscription billing engine (P2-T11).

Generates exactly one correct account.move invoice on purchase (activate) and
on each renewal, applies UAE VAT 5%, prorates mid-cycle upgrades, and tracks
payment status back onto the subscription. Uses Odoo's accounting engine
(FINANCIAL_PLATFORM_ARCHITECTURE §4/§5) — never a custom invoice model.
"""
import logging

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


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
    # Lifecycle-scheduler bookkeeping (P2-T14): which advance-warning thresholds
    # and dunning steps have already fired, so each fires exactly once per
    # subscription across daily cron runs. CSV of integers (e.g. "30,14").
    nc_warnings_sent = fields.Char(copy=False, default='')
    nc_dunning_sent = fields.Char(copy=False, default='')
    # P2-T17 email automation: the day the tenant last received ANY lifecycle
    # email (the "one lifecycle email per tenant per day" de-dup key), and a
    # once-only marker for the trial-ending reminder.
    nc_last_lifecycle_mail_date = fields.Date(copy=False)
    nc_trial_ending_sent = fields.Boolean(copy=False, default=False)

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
                if old and old != sub.plan_id:
                    if sub.status == 'active':
                        sub._nc_proration_invoice(old, sub.plan_id)
                    sub._nc_send_lifecycle_mail(
                        'mail_template_plan_change',
                        old_plan=old.name, new_plan=sub.plan_id.name)
        return res

    # ---- transition notices (P2-T17) -------------------------------------

    def action_expire(self):
        """Expire the subscription, THEN email the tenant an 'expired' notice.

        SIDE EFFECT — every call to this transition queues a lifecycle email to
        the tenant (capped to one lifecycle email per tenant per day by
        _nc_send_lifecycle_mail). This includes programmatic callers: the daily
        sweep (_nc_expire_due) and any admin/bulk expiry will each generate one
        email per subscription. Intended, but be aware before calling in a loop."""
        res = super().action_expire()
        for sub in self:
            sub._nc_send_lifecycle_mail('mail_template_expired')
        return res

    def action_suspend(self):
        """Suspend the subscription, THEN email the tenant a 'suspended' notice.

        SIDE EFFECT — like action_expire above, every call queues a (daily-de-
        duped) lifecycle email to the tenant, including the daily sweep
        (_nc_suspend_after_grace) and any admin/bulk suspension."""
        res = super().action_suspend()
        for sub in self:
            sub._nc_send_lifecycle_mail('mail_template_suspended')
        return res

    # ---- billing internals -----------------------------------------------

    def _nc_period_amount(self, plan=None):
        """Plan price for this subscription's billing cycle."""
        self.ensure_one()
        plan = plan or self.plan_id
        if self.billing_cycle == 'one_time':
            # #471: its own price, not a period price. Falling back to
            # monthly_price would charge a perpetual licence at one month's
            # rate; the plan carries one_time_price for exactly this.
            return plan.one_time_price
        return plan.yearly_price if self.billing_cycle == 'yearly' else plan.monthly_price

    def _nc_bill_period(self, reason, period_start, period_end):
        """Create the single invoice for this billing period (idempotent per
        period_start — the 'exactly one invoice' acceptance)."""
        self.ensure_one()
        if not self.plan_id:
            return self.env['account.move']  # arch-guard: admin-db-billing
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
            return self.env['account.move']  # arch-guard: admin-db-billing
        # Prorate over the CURRENT period only. The period runs one billing
        # cycle back from end_date — deriving it from start_date would span
        # every renewed cycle and shrink the daily rate after a renewal.
        delta = relativedelta(years=1) if self.billing_cycle == 'yearly' else relativedelta(months=1)
        period_start = self.end_date - delta
        total_days = (self.end_date - period_start).days or 1
        # Clamp to [0, total_days] so we never bill more than one full period's
        # difference (e.g. an upgrade before the renewed period has started).
        remaining = max(0, min((self.end_date - today).days, total_days))
        diff = self._nc_period_amount(new_plan) - self._nc_period_amount(old_plan)
        prorated = diff * remaining / total_days
        if prorated <= 0:  # only charge upgrades; downgrades adjust at renewal
            return self.env['account.move']  # arch-guard: admin-db-billing
        return self._nc_create_invoice(prorated, today, self.end_date, 'proration')

    def _nc_create_invoice(self, amount, period_start, period_end, reason):
        """Build + post one customer invoice via Odoo's accounting engine."""
        self.ensure_one()
        company = self.env.company
        # Ensure AED here, not only in post_init: the chart template loads via a
        # deferred precommit that resets the company currency to the template's
        # default (USD), so a currency switch done in the hook is clobbered.
        # At first-invoice time the registry is loaded and the chart is settled,
        # so this takes — and it is a no-op on every later invoice.
        company._nc_ensure_currency()
        partner = self.tenant_id._nc_ensure_partner()
        product = company._nc_billing_product()
        tax = company._nc_billing_tax()
        move = self.env['account.move'].create({  # arch-guard: admin-db-billing
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
                # #471: a one-time charge covers no period, so it gets a
                # perpetual label rather than "start → False".
                'name': ('%s — %s (perpetual, from %s)'
                         % (self.plan_id.name, reason, period_start)
                         if not period_end else
                         '%s — %s (%s → %s)' % (
                             self.plan_id.name, reason, period_start, period_end)),
                'quantity': 1.0,
                'price_unit': amount,
                'tax_ids': [(6, 0, tax.ids)],
            })],
        })
        move.action_post()
        return move

    # ---- payment collection (P2-T13) -------------------------------------

    def _nc_apply_payment(self, invoice):
        """A subscription invoice has been paid: extend the subscription to
        cover the paid period and reactivate a lapsed one.

        Extend-to-cover (end_date = max(end_date, period_end)) is idempotent and
        never double-counts against the period already granted at activation —
        paying the purchase invoice is a no-op on the date, paying a renewal
        invoice pushes the end out. A payment on an expired/suspended/trial
        subscription reactivates it (grace recovery / trial conversion)."""
        self.ensure_one()
        period_end = invoice.nc_period_end
        if period_end and (not self.end_date or period_end > self.end_date):
            self.end_date = period_end
        if self.status in ('expired', 'suspended', 'trial'):
            self.action_reactivate()
        self.message_post(body=self.env._(
            'Payment received for invoice %(inv)s — subscription active through %(end)s.',
            inv=invoice.name, end=self.end_date or '-'))
        self._nc_send_lifecycle_mail('mail_template_payment_received', invoice_name=invoice.name)

    def _nc_on_payment_failed(self, transaction):
        """A payment attempt failed. P2-T13 records it (chatter) and leaves a
        seam for the P2-T14 dunning scheduler; payment_status already surfaces
        an unpaid, past-due invoice as 'overdue'."""
        self.ensure_one()
        _logger.info("Payment failed for subscription %s (transaction %s, state %s)",
                     self.name, transaction.reference, transaction.state)
        self.message_post(body=self.env._(
            'Payment attempt failed for this subscription (transaction %(ref)s, %(state)s).',
            ref=transaction.reference, state=transaction.state))
        self._nc_send_lifecycle_mail('mail_template_payment_failed', reference=transaction.reference)

    # ---- lifecycle & dunning scheduler (P2-T14) --------------------------

    # Advance-warning thresholds (days before expiry) and dunning steps (days
    # after the invoice fell due). Each fires exactly once per subscription.
    _WARNING_DAYS = (30, 14, 7, 1)
    _DUNNING_DAYS = (1, 3, 7)
    _EXPIRY_BUFFER_DAYS = 2   # 48h safety buffer after end_date before expiring
    _TRIAL_ENDING_DAYS = 3    # a single trial-ending reminder this many days before

    @staticmethod
    def _nc_parse_sent(csv_value):
        return {int(x) for x in (csv_value or '').split(',') if x.strip()}

    def _nc_mark_sent(self, field, day):
        sent = self._nc_parse_sent(self[field])
        sent.add(day)
        self[field] = ','.join(str(d) for d in sorted(sent))

    @api.model
    def _cron_lifecycle_sweep(self, today=None):
        """Daily lifecycle + dunning sweep. `today` is injectable so tests drive
        it on a simulated clock; the ir.cron calls it with the real date. Every
        step is idempotent (per-subscription trackers + guarded transitions), so
        re-running on the same day changes nothing."""
        today = today or fields.Date.context_today(self)
        # Put the sweep's date on the context so every email queued during it —
        # recurring reminders AND the transitions it triggers — de-dups on this
        # (possibly simulated) day.
        sweep = self.with_context(nc_lifecycle_today=today)
        # Transitions first: their notices (expired/suspended) take the day's
        # single lifecycle-email slot ahead of recurring warnings/dunning (P2-T17).
        sweep._nc_sweep_trials(today)
        sweep._nc_expire_due(today)
        sweep._nc_suspend_after_grace(today)
        # Then the recurring reminders.
        sweep._nc_send_trial_ending_warnings(today)
        sweep._nc_send_expiry_warnings(today)
        sweep._nc_run_dunning(today)

    def _nc_active_with_end(self, today):
        return self.search([('status', '=', 'active'), ('end_date', '!=', False)])

    def _nc_send_expiry_warnings(self, today):
        """Email the most-urgent not-yet-sent threshold for each active sub."""
        for sub in self._nc_active_with_end(today):
            days_left = (sub.end_date - today).days
            if days_left < 0:
                continue
            sent = sub._nc_parse_sent(sub.nc_warnings_sent)
            for threshold in sorted(self._WARNING_DAYS):   # most urgent first
                if days_left <= threshold and threshold not in sent:
                    # One attempt per sweep (break regardless); mark only on a
                    # real send so a de-dup-suppressed warning retries next day.
                    if sub._nc_send_lifecycle_mail('mail_template_expiry_warning', days_left=days_left):
                        sub._nc_mark_sent('nc_warnings_sent', threshold)
                        sub.message_post(body=sub.env._(
                            'Expiry warning sent (%(d)s days before expiry).', d=threshold))
                    break

    def _nc_send_trial_ending_warnings(self, today):
        """Send a single 'your trial is ending' reminder as trial_end nears."""
        for sub in self.search([('status', '=', 'trial'),
                                ('trial_end_date', '!=', False),
                                ('nc_trial_ending_sent', '=', False)]):
            days_left = (sub.trial_end_date - today).days
            if 0 <= days_left <= self._TRIAL_ENDING_DAYS:
                # Mark only on a real send so a suppressed reminder retries.
                if sub._nc_send_lifecycle_mail('mail_template_trial_ending', days_left=days_left):
                    sub.nc_trial_ending_sent = True
                    sub.message_post(body=sub.env._('Trial-ending reminder sent.'))

    def _nc_sweep_trials(self, today):
        """Expire trials past their trial_end_date (trial -> expired). Done here
        against the injected `today` rather than the base _process_trial_expiry,
        which reads the real date and so cannot be driven on a simulated clock."""
        for sub in self.search([('status', '=', 'trial'), ('trial_end_date', '!=', False)]):
            if sub.trial_end_date <= today:
                sub.action_expire()

    def _nc_expire_due(self, today):
        """active -> expired once today is past end_date + the 48h buffer."""
        for sub in self.search([('status', '=', 'active'), ('end_date', '!=', False)]):
            if today >= sub.end_date + relativedelta(days=self._EXPIRY_BUFFER_DAYS):
                sub.action_expire()

    def _nc_suspend_after_grace(self, today):
        """expired -> suspended once today is past the grace window."""
        for sub in self.search([('status', '=', 'expired'), ('grace_end_date', '!=', False)]):
            if today > sub.grace_end_date:
                sub.action_suspend()

    def _nc_run_dunning(self, today):
        """Send dunning reminders on the retry schedule for overdue subscriptions
        — one email per step, keyed off the oldest past-due invoice's due date."""
        for sub in self.search([('payment_status', '=', 'overdue')]):
            due = sub._nc_oldest_overdue_due_date()
            if not due:
                continue
            days_over = (today - due).days
            sent = sub._nc_parse_sent(sub.nc_dunning_sent)
            for step in sorted(self._DUNNING_DAYS):
                if days_over >= step and step not in sent:
                    # Mark only on a real send so a suppressed reminder retries.
                    if sub._nc_send_lifecycle_mail('mail_template_dunning', days_over=days_over):
                        sub._nc_mark_sent('nc_dunning_sent', step)
                        sub.message_post(body=sub.env._(
                            'Dunning reminder sent (%(d)s days overdue).', d=step))
                    break

    def _nc_oldest_overdue_due_date(self):
        self.ensure_one()
        overdue = self.invoice_ids.filtered(
            lambda m: m.state == 'posted' and m.move_type == 'out_invoice'
            and m.payment_state == 'not_paid' and m.invoice_date_due)
        return min(overdue.mapped('invoice_date_due')) if overdue else False

    # Every lifecycle email renders through the P1-T18 branded notification
    # layout — the whole set (incl. the P2-T14 expiry/dunning mails) is branded
    # uniformly at send time, without editing each template.
    _LIFECYCLE_LAYOUT = 'mail.mail_notification_light'

    def _nc_lifecycle_mail_sent_today(self, today):
        """True if this tenant already received a lifecycle email today, across
        any of its subscriptions (the P2-T17 'never two on the same day' rule)."""
        self.ensure_one()
        if not self.tenant_id:
            return self.nc_last_lifecycle_mail_date == today
        return bool(self.search([
            ('tenant_id', '=', self.tenant_id.id),
            ('nc_last_lifecycle_mail_date', '=', today),
        ], limit=1))

    def _nc_send_lifecycle_mail(self, template_xmlid, **ctx):
        """Queue a branded lifecycle email to the tenant — at most one per tenant
        per day (P2-T17 de-dup) — never letting mail transport break the caller
        (dev has no SMTP → a queued mail.mail row).

        Returns True only if an email was actually queued; False when it was
        suppressed by the daily de-dup, the recipient/template is missing, or
        transport failed. Recurring callers (warnings, dunning, trial-ending)
        use this so they mark their trackers ONLY on a real send — a suppressed
        reminder stays unsent and is retried on the next eligible day (delay,
        not drop). Event-driven transition notices ignore the result.

        Callers are ordered so genuine TRANSITION notices (expired, suspended,
        payment, plan change) run before recurring warnings/dunning in a sweep,
        so the more important message wins the day's single slot."""
        self.ensure_one()
        template = self.env.ref(
            'ncollection_billing.%s' % template_xmlid, raise_if_not_found=False)
        if not template or not self.tenant_id.email:
            return False
        # The de-dup date follows the LIFECYCLE clock: the daily sweep injects
        # its `today` via context so a simulated-clock run (and any transition it
        # triggers) de-dups on the simulated day, not the real one. Direct
        # transition calls outside a sweep fall back to the real date.
        today = self.env.context.get('nc_lifecycle_today') or fields.Date.context_today(self)
        if self._nc_lifecycle_mail_sent_today(today):
            return False
        try:
            template.with_context(**ctx).send_mail(
                self.id, email_layout_xmlid=self._LIFECYCLE_LAYOUT, force_send=False)
            self.nc_last_lifecycle_mail_date = today
            return True
        except Exception:  # noqa: BLE001 - transport must never break the sweep
            _logger.warning("Lifecycle mail %s could not be queued for subscription %s",
                            template_xmlid, self.id, exc_info=True)
            return False

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
