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
        'draft': {'active', 'cancelled'},
        'active': {'expired', 'cancelled'},
        'expired': {'active'},   # renew after expiry
        'cancelled': set(),      # terminal
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
        ],
        default='monthly',
        required=True,
    )
    status = fields.Selection(
        selection=[
            ('draft', 'Draft'),
            ('active', 'Active'),
            ('expired', 'Expired'),
            ('cancelled', 'Cancelled'),
        ],
        default='draft',
        required=True,
        tracking=True,
    )
    days_remaining = fields.Integer(
        compute='_compute_days_remaining',
        help='Days until end_date. 0 when there is no end date or it has passed.',
    )

    @api.depends('end_date')
    def _compute_days_remaining(self):
        today = fields.Date.context_today(self)
        for sub in self:
            if sub.end_date and sub.end_date > today:
                sub.days_remaining = (sub.end_date - today).days
            else:
                sub.days_remaining = 0

    @api.constrains('start_date', 'end_date')
    def _check_dates(self):
        for sub in self:
            if sub.end_date and sub.start_date and sub.end_date <= sub.start_date:
                raise ValidationError(
                    self.env._('End date must be after start date (subscription "%s").', sub.name)
                )

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

    def action_renew(self):
        """active/expired -> active, extending end_date by one billing cycle.

        The new period starts from the later of today and the current
        end_date, so renewing early does not shorten the running period.
        """
        for sub in self:
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
