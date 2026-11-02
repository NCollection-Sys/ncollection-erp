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


@tagged('post_install', '-at_install')
class TestPurchaseLinesAndFileNames(TestPurchaseDuplicate):
    """LINE-01/02 and PRINT-01 on purchase orders."""

    def test_amount_follows_price_and_total_incl_tax_is_shown(self):
        from lxml import etree
        arch = etree.fromstring(self.env['purchase.order'].get_views([(False, 'form')])['views']['form']['arch'])
        fields = [(f.get('name'), f.get('string'))
                  for f in arch.xpath("//field[@name='order_line']/list")[0].findall('field')]
        names = [f[0] for f in fields]
        price = names.index('price_unit')
        self.assertEqual(fields[price + 1:price + 3],
                         [('price_subtotal', 'Amount'), ('price_total', 'Total incl. Tax')])

    def test_an_rfq_and_an_order_are_named_as_what_they_are(self):
        order = self._order()
        for xmlid in ('purchase.report_purchase_quotation', 'purchase.action_report_purchase_order'):
            self.assertEqual(self.env.ref(xmlid).print_report_name, 'object._nc_print_filename()')
        self.assertEqual(order._nc_print_filename(), 'Request for Quotation %s' % order.name.replace('/', '-'))
        order.button_confirm()
        self.assertEqual(order._nc_print_filename(), 'Purchase Order %s' % order.name.replace('/', '-'))


@tagged('post_install', '-at_install')
class TestPurchaseStampPerDocument(TestPurchaseDuplicate):
    """The Document Stamp switch on requests for quotation and purchase orders: Default follows
    the kind's Print Setting, On / Off applies to this one
    document, the printed PDF follows it, and only a Settings administrator may change it (no group is named on these
    kinds)."""

    def _stamp(self, code):
        from odoo import Command
        from odoo.addons.ncollection_branding.tests.test_document_stamp import _b64, _png
        kind = self.env.ref('ncollection_purchase_documents.doc_type_%s' % code)
        return self.env['ncollection.document.stamp'].create({
            'name': code, 'company_id': self.env.company.id, 'image': _b64(_png()),
            'document_type_ids': [Command.set(kind.ids)]})

    def test_each_order_kind_follows_its_own_switch(self):
        from odoo.addons.ncollection_branding.tests.stamp_print import stamps_on
        reports = {'purchase_rfq': self.env.ref('purchase.report_purchase_quotation'),
                   'purchase_order': self.env.ref('purchase.action_report_purchase_order')}
        for confirm, code in ((False, 'purchase_rfq'), (True, 'purchase_order')):
            with self.subTest(code=code):
                report = reports[code]
                self._stamp(code)
                order, other = self._order(confirm=confirm), self._order(confirm=confirm)
                order, other = order.with_env(self.env), other.with_env(self.env)          # as the administrator
                self.assertEqual(order.nc_stamp_mode, 'default')
                self.assertEqual(stamps_on(report, order), [1])
                order.nc_stamp_mode = 'off'
                self.assertEqual(stamps_on(report, order), [0])
                self.assertEqual(stamps_on(report, other), [1], "the other order is unaffected")
                self.env.ref('ncollection_purchase_documents.doc_type_%s' % code).nc_show_stamp = False
                other.nc_stamp_mode = 'on'
                self.assertEqual(stamps_on(report, other), [1])
                self.assertEqual(stamps_on(report, self._order(confirm=confirm)), [0])

    def test_only_a_settings_administrator_may_switch_it(self):
        order = self._order()
        user = self.buyer
        self.assertFalse(order.with_user(user).nc_stamp_mode_editable)
        with self.assertRaises(AccessError):
            order.with_user(user).write({'nc_stamp_mode': 'off'})
        self.assertEqual(order.nc_stamp_mode, 'default')
