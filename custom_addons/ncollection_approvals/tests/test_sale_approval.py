# -*- coding: utf-8 -*-
"""P3-T07 acceptance: a threshold-crossing SO cannot confirm until approved."""
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestSaleApproval(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.nc_sale_approval_threshold = 1000.0
        cls.partner = cls.env['res.partner'].create({'name': 'Approval Customer'})
        cls.product = cls.env['product.product'].create({
            'name': 'Approval Widget', 'list_price': 100.0})
        cls.manager = cls.env['res.users'].create({
            'login': 'nc_so_manager', 'name': 'SO Manager',
            'group_ids': [(6, 0, [cls.env.ref('sales_team.group_sale_manager').id])]})
        cls.salesperson = cls.env['res.users'].create({
            'login': 'nc_so_person', 'name': 'SO Person',
            'group_ids': [(6, 0, [cls.env.ref('sales_team.group_sale_salesman').id])]})

    def _order(self, qty):
        return self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id, 'product_uom_qty': qty,
                'price_unit': 100.0})]})

    def test_below_threshold_confirms_directly(self):
        so = self._order(5)  # 500 < 1000
        self.assertFalse(so._nc_needs_approval())
        so.action_confirm()
        self.assertEqual(so.state, 'sale')

    def test_above_threshold_blocked_until_manager_approves(self):
        so = self._order(20)  # 2000 > 1000
        self.assertTrue(so._nc_needs_approval())
        # THE acceptance: cannot confirm until the approval activity completes.
        with self.assertRaises(UserError):
            so.action_confirm()
        self.assertNotEqual(so.state, 'sale')
        # request approval -> an activity is scheduled, state to_approve
        so.action_request_approval()
        self.assertEqual(so.approval_state, 'to_approve')
        self.assertTrue(so.activity_ids)
        # a non-manager cannot approve — enforced at the ORM layer (Rule 4/7)
        with self.assertRaises(AccessError):
            so.with_user(self.salesperson).action_approve()
        self.assertEqual(so.approval_state, 'to_approve')
        # the manager approves (completes the activity) -> now it confirms
        so.with_user(self.manager).action_approve()
        self.assertEqual(so.approval_state, 'approved')
        self.assertFalse(so.activity_ids)  # the approval activity is done
        so.action_confirm()
        self.assertEqual(so.state, 'sale')

    def test_rejected_order_stays_blocked(self):
        so = self._order(20)
        so.action_request_approval()
        so.with_user(self.manager).action_reject()
        self.assertEqual(so.approval_state, 'rejected')
        with self.assertRaises(UserError):
            so.action_confirm()
