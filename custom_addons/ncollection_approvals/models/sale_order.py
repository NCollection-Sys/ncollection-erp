# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

_TODO_ACT = 'mail.mail_activity_data_todo'
_SALE_MANAGER = 'sales_team.group_sale_manager'


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    # P3-T07: a sale order above the company threshold needs a Sales Manager's
    # approval before it can be confirmed. 'none' when no approval is required.
    approval_state = fields.Selection(
        selection=[
            ('none', 'Not Required'),
            ('to_approve', 'To Approve'),
            ('approved', 'Approved'),
            ('rejected', 'Rejected'),
        ],
        default='none', copy=False, tracking=True, string='Approval')
    # Drives button visibility in the view (kept in sync with _nc_needs_approval).
    nc_approval_required = fields.Boolean(
        compute='_compute_nc_approval_required', string='Approval Required')
    # The order total at the moment of approval. An approval only covers the
    # amount it was granted for — growing past it voids the approval so it must
    # be requested again (blocks the "approve then inflate the order" bypass).
    nc_approved_amount = fields.Monetary(
        currency_field='currency_id', copy=False, readonly=True,
        string='Approved Amount')

    @api.depends('amount_total', 'currency_id',
                 'company_id.nc_sale_approval_threshold')
    def _compute_nc_approval_required(self):
        for order in self:
            order.nc_approval_required = order._nc_needs_approval()

    def _nc_needs_approval(self):
        """True when this order's total exceeds the company approval threshold.
        The threshold is stored in the COMPANY currency; convert it into the
        order's currency before comparing (mirrors Odoo core _approval_allowed —
        otherwise a foreign-currency order compares two different currencies)."""
        self.ensure_one()
        threshold = self.company_id.nc_sale_approval_threshold
        if threshold <= 0 or not self.currency_id:
            return False
        threshold = self.company_id.currency_id._convert(
            threshold, self.currency_id, self.company_id,
            self.date_order or fields.Date.context_today(self))
        return self.currency_id.compare_amounts(self.amount_total, threshold) > 0

    def _nc_approval_satisfied(self):
        """The order carries a *current* approval: it is approved and its total
        has not grown beyond the amount that was actually approved."""
        self.ensure_one()
        if self.approval_state != 'approved':
            return False
        return self.currency_id.compare_amounts(
            self.amount_total, self.nc_approved_amount) <= 0

    def _nc_approver_user(self):
        """A Sales Manager to assign the approval activity to (fallback: self)."""
        self.ensure_one()
        group = self.env.ref(_SALE_MANAGER, raise_if_not_found=False)
        manager = group.user_ids[:1] if group else self.env['res.users']
        if not manager:
            _logger.warning(
                "ncollection_approvals: no Sales Manager to receive the approval "
                "activity for %s; assigning to the requester.", self.name or '')
        return manager or self.env.user

    def _nc_check_approver(self):
        """Only a Sales Manager may set an order to Approved — enforced at the
        ORM layer, not just via the button's groups= (Rule 4/7; the #228 lesson).
        env.su (superuser / migrations) is trusted."""
        if not self.env.su and not self.env.user.has_group(_SALE_MANAGER):
            raise AccessError(self.env._(
                "Only a Sales Manager may approve sale orders."))

    # ---- state-write guard (the #228 pattern) ---------------------------
    # approval_state is a plain field; without this, a salesperson with write
    # access to their own order could set approval_state='approved' directly
    # over RPC and skip the manager gate. Guard the transition itself, not only
    # the action method.

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('approval_state') == 'approved':
                self._nc_check_approver()
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('approval_state') == 'approved':
            self._nc_check_approver()
        res = super().write(vals)
        # Post-approval edit: if the lines changed and the order now exceeds the
        # amount it was approved for, void the approval so it must be re-requested
        # (and the Request Approval button reappears).
        if 'order_line' in vals and 'approval_state' not in vals:
            stale = self.filtered(
                lambda o: o.approval_state == 'approved'
                and o._nc_needs_approval()
                and o.currency_id.compare_amounts(
                    o.amount_total, o.nc_approved_amount) > 0)
            if stale:
                stale.write({'approval_state': 'none', 'nc_approved_amount': 0.0})
                for order in stale:
                    order.message_post(body=self.env._(
                        "Order total increased beyond the approved amount; "
                        "approval was reset and must be requested again."))
        return res

    # ---- workflow -------------------------------------------------------

    def action_request_approval(self):
        """Send the order for manager approval: schedule the approval activity
        and move it to 'to_approve'. Re-requestable after a rejection."""
        for order in self:
            if not order._nc_needs_approval():
                continue
            if order.approval_state not in ('none', 'rejected'):
                continue  # already in flight or approved — don't duplicate
            order.approval_state = 'to_approve'
            order.activity_schedule(
                _TODO_ACT,
                summary=self.env._("Approve sale order %s", order.name or ''),
                note=self.env._(
                    "This order's total (%(total)s) exceeds the approval "
                    "threshold and needs Sales Manager approval.",
                    total=order.amount_total),
                user_id=order._nc_approver_user().id)
        return True

    def action_approve(self):
        self._nc_check_approver()
        for order in self:
            if order.approval_state != 'to_approve':
                continue
            order.activity_feedback([_TODO_ACT])
            order.write({'approval_state': 'approved',
                         'nc_approved_amount': order.amount_total})
            order.message_post(body=self.env._("Approval granted."))
        return True

    def action_reject(self):
        self._nc_check_approver()
        for order in self:
            if order.approval_state != 'to_approve':
                continue
            order.activity_feedback([_TODO_ACT])
            order.approval_state = 'rejected'
            order.message_post(body=self.env._("Approval rejected."))
        return True

    def action_confirm(self):
        # The gate: a threshold-crossing order cannot confirm until it carries a
        # current approval. No side effects here (raising would roll them back) —
        # the request is an explicit action_request_approval.
        blocked = self.filtered(
            lambda o: o._nc_needs_approval() and not o._nc_approval_satisfied())
        if blocked:
            raise UserError(self.env._(
                "These sale orders exceed the approval threshold and need "
                "Sales Manager approval before confirmation (use 'Request "
                "Approval'): %s", ", ".join(blocked.mapped('name'))))
        return super().action_confirm()
