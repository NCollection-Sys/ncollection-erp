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
