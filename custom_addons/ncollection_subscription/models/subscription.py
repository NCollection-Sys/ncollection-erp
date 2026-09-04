from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class Subscription(models.Model):
    _name = 'ncollection.subscription'
    _description = 'NCollection Subscription'
    _order = 'start_date desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    # Guarded lifecycle: current status -> statuses allowed to move to.
    # Single source of truth for every transition method below.
    _ALLOWED_TRANSITIONS = {
        'draft': {'trial', 'active', 'cancelled'},
        'trial': {'active', 'expired', 'cancelled'},   # active = trial conversion
        'active': {'expired', 'suspended', 'cancelled'},
        'expired': {'active', 'suspended'},   # active = renew / reactivate in grace
        'suspended': {'active', 'terminated'},   # active = reactivation
        'cancelled': set(),      # terminal
        'terminated': set(),     # terminal
    }
    # Statuses from which action_renew is allowed (renew from 'active' keeps
    # the status and only extends the period, so it is not in the map above).
    _RENEWABLE_STATUSES = {'active', 'expired'}

    name = fields.Char(required=True, copy=False, default='New')
    tenant_id = fields.Many2one('ncollection.tenant', string='Tenant', required=True, tracking=True)
    plan_id = fields.Many2one('ncollection.subscription.plan', string='Plan', required=True, tracking=True)
    start_date = fields.Date(required=True, default=fields.Date.context_today)
    end_date = fields.Date()
    billing_cycle = fields.Selection(
        selection=[
            ('monthly', 'Monthly'),
            ('yearly', 'Yearly'),
            # #471: a perpetual membership. Paid once, never renewed, and it
            # has NO end_date — the absence of a date is what encodes "no
            # expiry", not a date far in the future. Every recurring path in
            # the platform is already keyed on `end_date != False`
            # (_nc_expire_due, the expiry warnings, grace_end_date,
            # days_remaining), so a subscription with no end date is invisible
            # to all of them by construction rather than by a special case
            # bolted onto each. `is_one_time` below is what the parts that DO
            # need to know ask.
            ('one_time', 'One Time'),
        ],
        default='monthly',
        required=True,
        help='One Time is a perpetual membership: charged once, never renewed, '
             'and it stays active until an administrator cancels, suspends or '
             'terminates it.',
    )
    is_one_time = fields.Boolean(
        string='Perpetual',
        compute='_compute_is_one_time', store=True,
        help='This subscription never expires and is never renewed.')
    # MRR (P2-T15): the plan price normalized to a monthly amount. Stored so the
    # revenue graph/pivot can aggregate it natively. currency comes from the plan.
    currency_id = fields.Many2one(
        'res.currency', related='plan_id.currency_id', store=True, readonly=True)
    mrr = fields.Monetary(
        string='MRR', compute='_compute_mrr', store=True, currency_field='currency_id',
        help='Monthly Recurring Revenue: plan price normalized to monthly.')
    status = fields.Selection(
        selection=[
            ('draft', 'Draft'),
            ('trial', 'Trial'),
            ('active', 'Active'),
            ('expired', 'Expired'),
            ('suspended', 'Suspended'),
            ('cancelled', 'Cancelled'),
            ('terminated', 'Terminated'),
        ],
        default='draft',
        required=True,
        tracking=True,
    )
    trial_end_date = fields.Date(
        help='When the trial ends. Set by action_start_trial from the plan '
             'trial_days; a trial past this date is converted or expired.',
    )
    grace_end_date = fields.Date(
        compute='_compute_grace_end_date', store=True,
        help='End of the post-expiry grace window (end_date + plan grace_days). '
             'During grace the subscription is expired but access continues; '
             'suspension only happens after this date (fired by P2-T14).',
    )
    days_remaining = fields.Integer(
        compute='_compute_days_remaining',
        help='Days until end_date. 0 when there is no end date or it has passed.',
    )

    @api.depends('end_date', 'plan_id.grace_days')
    def _compute_grace_end_date(self):
        for sub in self:
            if sub.end_date:
                sub.grace_end_date = sub.end_date + relativedelta(days=sub.plan_id.grace_days or 0)
            else:
                sub.grace_end_date = False

    @api.depends('end_date')
    def _compute_days_remaining(self):
        today = fields.Date.context_today(self)
        for sub in self:
            if sub.end_date and sub.end_date > today:
                sub.days_remaining = (sub.end_date - today).days
            else:
                sub.days_remaining = 0

    @api.depends('billing_cycle')
    def _compute_is_one_time(self):
        for sub in self:
            sub.is_one_time = sub.billing_cycle == 'one_time'

    @api.depends('plan_id', 'plan_id.monthly_price', 'plan_id.yearly_price', 'billing_cycle')
    def _compute_mrr(self):
        for sub in self:
            plan = sub.plan_id
            if not plan or sub.billing_cycle == 'one_time':
                # A perpetual membership has no recurring revenue by
                # definition (#471). Spreading its one-time price over months
                # would inflate MRR with money that will never come again.
                sub.mrr = 0.0
            elif sub.billing_cycle == 'yearly':
                sub.mrr = (plan.yearly_price or 0.0) / 12.0
            else:
                sub.mrr = plan.monthly_price or 0.0

    @api.constrains('start_date', 'end_date')
    def _check_dates(self):
        for sub in self:
            if sub.end_date and sub.start_date and sub.end_date <= sub.start_date:
                raise ValidationError(
                    self.env._('End date must be after start date (subscription "%s").', sub.name)
                )

    @api.constrains('billing_cycle', 'end_date')
    def _check_one_time_never_expires(self):
        """A One Time subscription must have NO end date (#471).

        This is the whole guarantee, enforced rather than documented: every
        recurring path — expiry, grace, suspension, warnings, renewal billing —
        keys on `end_date != False`, so an end date is exactly what would make a
        perpetual membership start behaving like a recurring one. Blocking it
        here means the promise cannot be broken by a form edit, an import, or a
        payment webhook writing a period end.
        """
        for sub in self:
            if sub.billing_cycle == 'one_time' and sub.end_date:
                raise ValidationError(self.env._(
                    'A One Time subscription never expires, so it cannot have '
                    'an end date (subscription "%s"). Cancel, suspend or '
                    'terminate it instead.', sub.name))

    # ------------------------------------------------------------------
    # Guarded lifecycle transitions
    # ------------------------------------------------------------------
    def _transition(self, new_status):
        for sub in self:
            allowed = self._ALLOWED_TRANSITIONS.get(sub.status, set())
            if new_status not in allowed:
                raise ValidationError(
                    self.env._(
                        'Invalid subscription transition: %(current)s -> %(new)s '
                        '(subscription "%(name)s").',
                        current=sub.status, new=new_status, name=sub.name,
                    )
                )
        self.write({'status': new_status})

    def action_activate(self):
        """draft -> active."""
        self._transition('active')

    def action_expire(self):
        """active -> expired."""
        self._transition('expired')

    def action_cancel(self):
        """draft/active -> cancelled (terminal)."""
        self._transition('cancelled')

    def action_start_trial(self):
        """draft -> trial, stamping trial_end_date from the plan's trial_days.

        A trial has full plan access (the tenant is provisioned by the SaaS
        layer); no invoice is raised until it converts to active."""
        today = fields.Date.context_today(self)
        for sub in self:
            days = sub.plan_id.trial_days or 0
            sub.trial_end_date = today + relativedelta(days=days)
        self._transition('trial')

    def action_suspend(self):
        """active/expired -> suspended (access is blocked; the SaaS layer
        projects this to the tenant so the P2-T03 interstitial fires)."""
        self._transition('suspended')

    def action_reactivate(self):
        """suspended/expired -> active (reactivation, incl. during grace)."""
        self._transition('active')

    def action_terminate(self):
        """suspended -> terminated (terminal end of life)."""
        self._transition('terminated')

    def _process_trial_expiry(self):
        """Expire trials whose trial_end_date has passed (trial -> expired).

        A pure, clock-free sweep over `self`: the daily scheduler that calls it
        on all trials is P2-T14. Auto-conversion on payment is P2-T13, so an
        un-converted trial defaults to expiry (which opens the grace window)."""
        today = fields.Date.context_today(self)
        due = self.filtered(
            lambda s: s.status == 'trial' and s.trial_end_date and s.trial_end_date <= today)
        due.action_expire()
        return due

    def action_renew(self):
        """active/expired -> active, extending end_date by one billing cycle.

        The new period starts from the later of today and the current
        end_date, so renewing early does not shorten the running period.
        """
        for sub in self:
            if sub.billing_cycle == 'one_time':
                # #471: refused, not silently ignored. A renewal here would
                # stamp an end_date and turn a perpetual membership into a
                # recurring one — and the billing layer would raise a renewal
                # invoice for a customer who already paid once, in full.
                raise ValidationError(self.env._(
                    'A One Time subscription is perpetual and cannot be '
                    'renewed (subscription "%s").', sub.name))
            if sub.status not in self._RENEWABLE_STATUSES:
                raise ValidationError(
                    self.env._(
                        'Invalid subscription transition: cannot renew from '
                        '%(current)s (subscription "%(name)s").',
                        current=sub.status, name=sub.name,
                    )
                )
        today = fields.Date.context_today(self)
        for sub in self:
            base = max(sub.end_date, today) if sub.end_date else today
            delta = relativedelta(years=1) if sub.billing_cycle == 'yearly' else relativedelta(months=1)
            sub.write({'status': 'active', 'end_date': base + delta})

    # ------------------------------------------------------------------
    # Chatter
    # ------------------------------------------------------------------
    def _track_subtype(self, init_values):
        self.ensure_one()
        if 'status' in init_values:
            return self.env.ref('ncollection_subscription.mt_subscription_status')
        return super()._track_subtype(init_values)
