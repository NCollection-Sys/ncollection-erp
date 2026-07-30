# -*- coding: utf-8 -*-
"""P3-T07: purchases above the threshold need two-level (department, then
finance) approval before they can be confirmed."""
from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPurchaseApproval(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.nc_purchase_approval_threshold = 1000.0
        cls.vendor = cls.env['res.partner'].create({'name': 'Approval Vendor'})
        cls.product = cls.env['product.product'].create({
            'name': 'PO Widget', 'list_price': 100.0, 'standard_price': 100.0})
        po_user = cls.env.ref('purchase.group_purchase_user').id
        cls.dept = cls.env['res.users'].create({
            'login': 'nc_po_dept', 'name': 'PO Dept',
            'group_ids': [(6, 0, [
                po_user,
                cls.env.ref('ncollection_approvals.group_approval_purchase_department').id])]})
        cls.finance = cls.env['res.users'].create({
            'login': 'nc_po_fin', 'name': 'PO Finance',
            'group_ids': [(6, 0, [
                po_user,
                cls.env.ref('ncollection_approvals.group_approval_purchase_finance').id])]})
        # A plain purchase user with neither approver group.
        cls.buyer = cls.env['res.users'].create({
            'login': 'nc_po_buyer', 'name': 'PO Buyer',
            'group_ids': [(6, 0, [po_user])]})

    def _po(self, qty):
        return self.env['purchase.order'].create({
            'partner_id': self.vendor.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id, 'name': 'PO Widget',
                'product_qty': qty, 'price_unit': 100.0,
                'product_uom_id': self.product.uom_id.id,
                'date_planned': fields.Datetime.now()})]})

    def test_below_threshold_confirms_directly(self):
        po = self._po(1)  # 100 < 1000
        self.assertFalse(po._nc_needs_approval())
        po.button_confirm()
        self.assertEqual(po.state, 'purchase')

    def test_two_level_approval_flow(self):
        po = self._po(20)  # 2000 > 1000
        with self.assertRaises(UserError):
            po.button_confirm()
        po.action_request_approval()
        self.assertEqual(po.approval_state, 'pending_department')
        # finance cannot skip the department level
        with self.assertRaises(AccessError):
            po.with_user(self.finance).action_approve_department()
        # department approves -> pending finance
        po.with_user(self.dept).action_approve_department()
        self.assertEqual(po.approval_state, 'pending_finance')
        # department cannot do the finance level
        with self.assertRaises(AccessError):
            po.with_user(self.dept).action_approve_finance()
        # finance approves -> approved -> confirms
        po.with_user(self.finance).action_approve_finance()
        self.assertEqual(po.approval_state, 'approved')
        po.button_confirm()
        self.assertEqual(po.state, 'purchase')

    def test_rejected_po_stays_blocked(self):
        po = self._po(20)  # 2000 > 1000
        po.action_request_approval()
        po.with_user(self.dept).action_reject()
        self.assertEqual(po.approval_state, 'rejected')
        with self.assertRaises(UserError):
            po.button_confirm()

    def test_forged_approval_state_is_blocked(self):
        # The #228 state-write guard: a plain purchase user (no approver group)
        # cannot set the confirm-affecting states directly over RPC.
        po = self._po(20)
        with self.assertRaises(AccessError):
            po.with_user(self.buyer).write({'approval_state': 'approved'})
        # a department approver still cannot jump straight to 'approved'
        with self.assertRaises(AccessError):
            po.with_user(self.dept).write({'approval_state': 'approved'})
        # nor can a plain user skip the department step
        with self.assertRaises(AccessError):
            po.with_user(self.buyer).write({'approval_state': 'pending_finance'})

    def test_approval_voided_when_order_grows(self):
        po = self._po(20)  # 2000
        po.action_request_approval()
        po.with_user(self.dept).action_approve_department()
        po.with_user(self.finance).action_approve_finance()
        self.assertEqual(po.approval_state, 'approved')
        po.write({'order_line': [(0, 0, {
            'product_id': self.product.id, 'name': 'PO Widget',
            'product_qty': 20, 'price_unit': 100.0,
            'product_uom_id': self.product.uom_id.id,
            'date_planned': fields.Datetime.now()})]})  # +2000 -> 4000
        self.assertEqual(po.approval_state, 'none')
        with self.assertRaises(UserError):
            po.button_confirm()
