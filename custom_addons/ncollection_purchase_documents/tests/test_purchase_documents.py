# -*- coding: utf-8 -*-
"""Request for quotation / purchase order: Duplicate. Synthetic data only."""
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPurchaseDuplicate(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Synthetic Vendor LLC'})
        cls.product = cls.env['product.product'].create({'name': 'Synthetic Part', 'standard_price': 8.0})
        cls.buyer = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'pd_buyer', 'login': 'pd_buyer',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('purchase.group_purchase_user').id])]})
        cls.stranger = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'pd_none', 'login': 'pd_none', 'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])]})

    def _order(self, confirm=False):
        order = self.env['purchase.order'].create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_qty': 4.0, 'price_unit': 8.0})]})
        if confirm:
            order.button_confirm()
        return order

    def _duplicate(self, order, user=None):
        action = order.with_user(user or self.buyer).action_nc_duplicate()
        return self.env['purchase.order'].browse(action['res_id'])

    def test_the_order_form_opts_in_to_the_partner_role_badges_and_keeps_its_context(self):
        from lxml import etree
        arch = etree.fromstring(self.env['purchase.order'].get_views([(False, 'form')])['views']['form']['arch'])
        # the partner fields the view declares; Odoo also adds invisible copies a modifier needs (no context, no
        # dropdown), which are not where anyone picks a partner
        contexts = [n.get('context') for n in arch.xpath("//field[@name='partner_id']") if n.get('context')]
        self.assertTrue(contexts)
        self.assertTrue(all('nc_partner_roles' in c for c in contexts), contexts)
        self.assertTrue(any("'res_partner_search_mode': 'supplier'" in c for c in contexts), contexts)

    def test_an_rfq_duplicates_into_a_new_rfq(self):
        original = self._order()
        before = (original.state, original.name, original.amount_total, original.write_date)
        new = self._duplicate(original)
        self.assertNotEqual(new, original)
        self.assertEqual(new.state, 'draft')
        self.assertNotEqual(new.name, original.name)
        self.assertEqual(new.partner_id, original.partner_id)
        self.assertEqual(new.order_line.mapped('product_qty'), [4.0])
        self.assertEqual((original.state, original.name, original.amount_total, original.write_date), before)

    def test_a_confirmed_order_duplicates_into_a_draft_with_nothing_received_or_billed(self):
        original = self._order(confirm=True)
        self.assertEqual(original.state, 'purchase')
        new = self._duplicate(original)
        self.assertEqual(new.state, 'draft')
        self.assertFalse(new.invoice_ids)
        self.assertEqual(new.order_line.mapped('qty_received'), [0.0])
        self.assertEqual(new.order_line.mapped('qty_invoiced'), [0.0])
        self.assertEqual(original.state, 'purchase')

    def test_duplicates_get_distinct_numbers(self):
        original = self._order()
        self.assertEqual(len({original.name, self._duplicate(original).name, self._duplicate(original).name}), 3)

    def test_a_user_who_cannot_create_gets_no_copy(self):
        original = self._order()
        count = self.env['purchase.order'].search_count([])
        with self.assertRaises(AccessError):
            original.with_user(self.stranger).action_nc_duplicate()
        self.assertEqual(self.env['purchase.order'].search_count([]), count)

    def test_the_action_is_not_a_sudo(self):
        self.assertEqual(self._duplicate(self._order(), self.buyer).create_uid, self.buyer)

    def test_the_button_is_in_the_header_and_translated(self):
        arch = self.env['purchase.order'].get_views([(False, 'form')])['views']['form']['arch']
        self.assertIn('name="action_nc_duplicate"', arch)
        self.env['res.lang']._activate_lang('ar_001')
        self.env['ir.module.module']._load_module_terms(['ncollection_purchase_documents'], ['ar_001'], overwrite=True)
        arabic = self.env['purchase.order'].with_context(lang='ar_001').get_views(
            [(False, 'form')])['views']['form']['arch']
        self.assertIn('نسخة مماثلة', arabic)
