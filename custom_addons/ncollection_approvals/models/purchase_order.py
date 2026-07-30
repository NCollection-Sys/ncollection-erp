# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

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

    @api.depends('amount_total', 'currency_id',
                 'company_id.nc_purchase_approval_threshold')
    def _compute_nc_approval_required(self):
        for order in self:
            order.nc_approval_required = order._nc_needs_approval()

    def _nc_needs_approval(self):
        self.ensure_one()
        threshold = self.company_id.nc_purchase_approval_threshold
        if threshold <= 0 or not self.currency_id:
            return False
        return self.currency_id.compare_amounts(self.amount_total, threshold) > 0

    def _nc_group_approver(self, group_xmlid):
        """A user in the given approver group to assign the activity to."""
        self.ensure_one()
        group = self.env.ref(group_xmlid, raise_if_not_found=False)
        approver = group.user_ids[:1] if group else self.env['res.users']
        return approver or self.env.user

    def _nc_schedule(self, group_xmlid, summary):
        self.ensure_one()
        self.activity_schedule(
            _TODO_ACT, summary=summary,
            note=self.env._(
                "This purchase order's total (%(total)s) exceeds the approval "
                "threshold.", total=self.amount_total),
            user_id=self._nc_group_approver(group_xmlid).id)

    # ---- workflow -------------------------------------------------------

    def action_request_approval(self):
        for order in self:
            if not order._nc_needs_approval():
                continue
            order.approval_state = 'pending_department'
            order._nc_schedule(
                _DEPT_GROUP,
                self.env._("Department approval: purchase order %s", order.name or ''))
        return True

    def _nc_check_group(self, group_xmlid, level):
        if not self.env.user.has_group(group_xmlid):
            raise AccessError(self.env._(
                "Only a %s approver may act on this purchase order.", level))

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
            order.approval_state = 'approved'
            order.message_post(body=self.env._("Finance approval granted."))
        return True

    def action_reject(self):
        # Either approver group may reject a PO that is still in an approval step.
        if not (self.env.user.has_group(_DEPT_GROUP)
                or self.env.user.has_group(_FIN_GROUP)):
            raise AccessError(self.env._(
                "Only a department or finance approver may reject this order."))
        for order in self:
            order.activity_feedback([_TODO_ACT])
            order.approval_state = 'rejected'
            order.message_post(body=self.env._("Approval rejected."))
        return True

    def button_confirm(self):
        blocked = self.filtered(
            lambda o: o._nc_needs_approval() and o.approval_state != 'approved')
        if blocked:
            raise UserError(self.env._(
                "These purchase orders exceed the approval threshold and need "
                "two-level approval before confirmation (use 'Request "
                "Approval'): %s", ", ".join(blocked.mapped('name'))))
        return super().button_confirm()
