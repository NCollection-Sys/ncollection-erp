# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

_TODO_ACT = 'mail.mail_activity_data_todo'
_DEPT_GROUP = 'ncollection_approvals.group_approval_purchase_department'
_FIN_GROUP = 'ncollection_approvals.group_approval_purchase_finance'


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    # P3-T07: a PO above the company threshold needs two-level approval —
    # department first, then finance — before it can be confirmed.
    approval_state = fields.Selection(
        selection=[
            ('none', 'Not Required'),
            ('pending_department', 'Pending Department'),
            ('pending_finance', 'Pending Finance'),
            ('approved', 'Approved'),
            ('rejected', 'Rejected'),
        ],
        default='none', copy=False, tracking=True, string='Approval')
    nc_approval_required = fields.Boolean(
        compute='_compute_nc_approval_required', string='Approval Required')
    # The order total at the moment of finance approval — see the sale.order
    # counterpart; growing past it voids the approval (re-request required).
    nc_approved_amount = fields.Monetary(
        currency_field='currency_id', copy=False, readonly=True,
        string='Approved Amount')

    @api.depends('amount_total', 'currency_id',
                 'company_id.nc_purchase_approval_threshold')
    def _compute_nc_approval_required(self):
        for order in self:
            order.nc_approval_required = order._nc_needs_approval()

    def _nc_needs_approval(self):
        """True when the PO total exceeds the company threshold. The threshold is
        in the COMPANY currency; convert it into the order's currency first
        (mirrors Odoo core, else a foreign-currency PO compares two currencies)."""
        self.ensure_one()
        threshold = self.company_id.nc_purchase_approval_threshold
        if threshold <= 0 or not self.currency_id:
            return False
        threshold = self.company_id.currency_id._convert(
            threshold, self.currency_id, self.company_id,
            self.date_order or fields.Date.context_today(self))
        return self.currency_id.compare_amounts(self.amount_total, threshold) > 0

    def _nc_approval_satisfied(self):
        """Approved and the total has not grown beyond the approved amount."""
        self.ensure_one()
        if self.approval_state != 'approved':
            return False
        return self.currency_id.compare_amounts(
            self.amount_total, self.nc_approved_amount) <= 0

    def _nc_group_approver(self, group_xmlid):
        """A user in the given approver group to assign the activity to."""
        self.ensure_one()
        group = self.env.ref(group_xmlid, raise_if_not_found=False)
        approver = group.user_ids[:1] if group else self.env['res.users']
        if not approver:
            _logger.warning(
                "ncollection_approvals: approver group %s has no members; the "
                "approval activity for %s falls back to the requester.",
                group_xmlid, self.name or '')
        return approver or self.env.user

    def _nc_schedule(self, group_xmlid, summary):
        self.ensure_one()
        self.activity_schedule(
            _TODO_ACT, summary=summary,
            note=self.env._(
                "This purchase order's total (%(total)s) exceeds the approval "
                "threshold.", total=self.amount_total),
            user_id=self._nc_group_approver(group_xmlid).id)

    def _nc_check_group(self, group_xmlid, level):
        if not self.env.su and not self.env.user.has_group(group_xmlid):
            raise AccessError(self.env._(
                "Only a %s approver may act on this purchase order.", level))

    def _nc_guard_state_write(self, state):
        """The #228 pattern: approval_state is a plain field. Guard the two
        confirm-affecting transitions so a plain purchase user cannot set them
        directly over RPC and skip the department/finance gate. env.su trusted."""
        if self.env.su or not state:
            return
        if state == 'approved' and not self.env.user.has_group(_FIN_GROUP):
            raise AccessError(self.env._(
                "Only a finance approver may set a purchase order to Approved."))
        if state == 'pending_finance' and not self.env.user.has_group(_DEPT_GROUP):
            raise AccessError(self.env._(
                "Only a department approver may advance a purchase order to "
                "finance approval."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._nc_guard_state_write(vals.get('approval_state'))
        return super().create(vals_list)

    def write(self, vals):
        self._nc_guard_state_write(vals.get('approval_state'))
        res = super().write(vals)
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
        for order in self:
            if not order._nc_needs_approval():
                continue
            if order.approval_state not in ('none', 'rejected'):
                continue
            order.approval_state = 'pending_department'
            order._nc_schedule(
                _DEPT_GROUP,
                self.env._("Department approval: purchase order %s", order.name or ''))
        return True

    def action_approve_department(self):
        self._nc_check_group(_DEPT_GROUP, 'department')
        for order in self:
            if order.approval_state != 'pending_department':
                continue
            order.activity_feedback([_TODO_ACT])
            order.approval_state = 'pending_finance'
            order._nc_schedule(
                _FIN_GROUP,
                self.env._("Finance approval: purchase order %s", order.name or ''))
            order.message_post(body=self.env._("Department approval granted."))
        return True

    def action_approve_finance(self):
        self._nc_check_group(_FIN_GROUP, 'finance')
        for order in self:
            if order.approval_state != 'pending_finance':
                continue
            order.activity_feedback([_TODO_ACT])
            order.write({'approval_state': 'approved',
                         'nc_approved_amount': order.amount_total})
            order.message_post(body=self.env._("Finance approval granted."))
        return True

    def action_reject(self):
        # Either approver group may reject a PO that is still in an approval step.
        if not (self.env.su or self.env.user.has_group(_DEPT_GROUP)
                or self.env.user.has_group(_FIN_GROUP)):
            raise AccessError(self.env._(
                "Only a department or finance approver may reject this order."))
        for order in self:
            if order.approval_state not in ('pending_department', 'pending_finance'):
                continue
            order.activity_feedback([_TODO_ACT])
            order.approval_state = 'rejected'
            order.message_post(body=self.env._("Approval rejected."))
        return True

    def button_confirm(self):
        blocked = self.filtered(
            lambda o: o._nc_needs_approval() and not o._nc_approval_satisfied())
        if blocked:
            raise UserError(self.env._(
                "These purchase orders exceed the approval threshold and need "
                "two-level approval before confirmation (use 'Request "
                "Approval'): %s", ", ".join(blocked.mapped('name'))))
        return super().button_confirm()
