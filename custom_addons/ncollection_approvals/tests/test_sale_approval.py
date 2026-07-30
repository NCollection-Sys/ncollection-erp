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
        # Pin the order currency to the company currency so the threshold (in
        # company currency) and the order total are the same currency — the full
        # CI DB installs the UAE localization, which sets the company currency to
        # AED while the default pricelist is USD; without this the conversion is
        # real and the fixed 1000/500 amounts are no longer comparable by eye.
        cls.pricelist = cls.env['product.pricelist'].create({
            'name': 'NC Approval Test PL',
            'currency_id': cls.company.currency_id.id})
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
            'pricelist_id': self.pricelist.id,
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

    def test_rejected_order_can_be_resubmitted(self):
        # A rejected order still above threshold must not be a dead end.
        so = self._order(20)
        so.action_request_approval()
        so.with_user(self.manager).action_reject()
        so.action_request_approval()  # resubmit
        self.assertEqual(so.approval_state, 'to_approve')
        so.with_user(self.manager).action_approve()
        so.action_confirm()
        self.assertEqual(so.state, 'sale')

    def test_forged_approval_state_is_blocked(self):
        # The #228 state-write guard: a salesperson cannot self-approve by
        # writing approval_state directly, even on their own order.
        so = self._order(20)
        so.user_id = self.salesperson  # give write access via the personal rule
        with self.assertRaises(AccessError):
            so.with_user(self.salesperson).write({'approval_state': 'approved'})
        self.assertNotEqual(so.approval_state, 'approved')

    def test_threshold_respects_currency_conversion(self):
        # A foreign-currency order is compared to the company-currency threshold
        # AFTER conversion, not by raw numbers (HIGH: the currency-mismatch fix).
        # Rate is defined relative to whatever the company currency is, so this
        # is deterministic whether the base currency is USD or AED.
        other = self.env['res.currency'].create({
            'name': 'NCT', 'symbol': 'N¢', 'rounding': 0.01})
        self.env['res.currency.rate'].create({
            'name': '2020-01-01', 'currency_id': other.id,
            'rate': 4.0, 'company_id': self.company.id})  # 1 company-ccy = 4 NCT
        pl = self.env['product.pricelist'].create({
            'name': 'NCT PL', 'currency_id': other.id})

        def order(qty):
            return self.env['sale.order'].create({
                'partner_id': self.partner.id, 'pricelist_id': pl.id,
                'order_line': [(0, 0, {
                    'product_id': self.product.id, 'product_uom_qty': qty,
                    'price_unit': 100.0})]})
        # 3000 NCT = 750 company-ccy < 1000 -> no approval (naive raw compare of
        # 3000 > 1000 would wrongly demand it).
        self.assertFalse(order(30)._nc_needs_approval())
        # 5000 NCT = 1250 company-ccy > 1000 -> approval required.
        self.assertTrue(order(50)._nc_needs_approval())

    def test_approval_voided_when_order_grows(self):
        # Approve, then inflate the order past the approved amount -> the stale
        # approval is voided and it cannot confirm until re-approved.
        so = self._order(20)  # 2000
        so.action_request_approval()
        so.with_user(self.manager).action_approve()
        self.assertEqual(so.approval_state, 'approved')
        so.write({'order_line': [(0, 0, {
            'product_id': self.product.id, 'product_uom_qty': 20,
            'price_unit': 100.0})]})  # +2000 -> 4000
        self.assertEqual(so.approval_state, 'none')
        with self.assertRaises(UserError):
            so.action_confirm()
