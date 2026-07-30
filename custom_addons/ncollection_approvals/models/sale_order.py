# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

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

    @api.depends('amount_total', 'currency_id',
                 'company_id.nc_sale_approval_threshold')
    def _compute_nc_approval_required(self):
        for order in self:
            order.nc_approval_required = order._nc_needs_approval()

    def _nc_needs_approval(self):
        """True when this order's total exceeds the company approval threshold."""
        self.ensure_one()
        threshold = self.company_id.nc_sale_approval_threshold
        if threshold <= 0 or not self.currency_id:
            return False
        return self.currency_id.compare_amounts(self.amount_total, threshold) > 0

    def _nc_approver_user(self):
        """A Sales Manager to assign the approval activity to (fallback: self)."""
        self.ensure_one()
        group = self.env.ref(_SALE_MANAGER, raise_if_not_found=False)
        manager = group.user_ids[:1] if group else self.env['res.users']
        return manager or self.env.user

    # ---- workflow -------------------------------------------------------

    def action_request_approval(self):
        """Send the order for manager approval: schedule the approval activity
        and move it to 'to_approve'."""
        for order in self:
            if not order._nc_needs_approval():
                continue
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

    def _nc_check_approver(self):
        """Only a Sales Manager may approve/reject — enforced at the ORM layer,
        not just via the button's groups= (Rule 4/7; the #228 lesson)."""
        if not self.env.user.has_group(_SALE_MANAGER):
            raise AccessError(self.env._(
                "Only a Sales Manager may approve or reject sale orders."))

    def action_approve(self):
        self._nc_check_approver()
        for order in self:
            order.activity_feedback([_TODO_ACT])
            order.approval_state = 'approved'
            order.message_post(body=self.env._("Approval granted."))
        return True

    def action_reject(self):
        self._nc_check_approver()
        for order in self:
            order.activity_feedback([_TODO_ACT])
            order.approval_state = 'rejected'
            order.message_post(body=self.env._("Approval rejected."))
        return True

    def action_confirm(self):
        # The gate: a threshold-crossing order cannot confirm until it is
        # approved. No side effects here (raising would roll them back) — the
        # request is an explicit action_request_approval.
        blocked = self.filtered(
            lambda o: o._nc_needs_approval() and o.approval_state != 'approved')
        if blocked:
            raise UserError(self.env._(
                "These sale orders exceed the approval threshold and need "
                "Sales Manager approval before confirmation (use 'Request "
                "Approval'): %s", ", ".join(blocked.mapped('name'))))
        return super().action_confirm()
