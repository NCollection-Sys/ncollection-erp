# -*- coding: utf-8 -*-
"""Purchase opens on Vendor Bills: New is a vendor bill straight away; RFQs / orders stay available. Synthetic."""
from odoo import Command, fields
from odoo.exceptions import AccessError
from odoo.tests import HttpCase, TransactionCase, tagged
from odoo.tools.safe_eval import safe_eval


@tagged('post_install', '-at_install')
class TestPurchaseBillsFirst(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.vendor = cls.env['res.partner'].create({'name': 'Synthetic Vendor LLC'})
        cls.product = cls.env['product.product'].create({'name': 'Synthetic Part', 'standard_price': 8.0,
                                                         'purchase_method': 'purchase'})
        cls.menu = cls.env.ref('ncollection_purchase_documents.menu_purchase_nc_vendor_bills')

    def _order(self):
        return self.env['purchase.order'].create({
            'partner_id': self.vendor.id,
            'order_line': [Command.create({'product_id': self.product.id, 'product_qty': 4, 'price_unit': 8.0})]})

    def test_vendor_bills_is_the_first_purchase_entry_and_reuses_the_accounting_action(self):
        root = self.env.ref('purchase.menu_purchase_root')
        self.assertEqual(self.menu.parent_id, root)
        self.assertEqual(root.child_id.sorted(lambda m: (m.sequence, m.id))[:1], self.menu)
        self.assertEqual(self.menu.action, self.env.ref('account.action_move_in_invoice_type'))
        self.assertEqual(self.menu.group_ids, self.env.ref('account.group_account_invoice'))
        self.assertTrue(self.env.ref('purchase.menu_purchase_rfq').active)

    def test_new_from_the_menu_is_a_vendor_bill_with_no_order_behind_it(self):
        context = safe_eval(self.menu.action.context or '{}', {'uid': self.env.uid, 'context': {}})
        Move = self.env['account.move'].with_context(**context)
        self.assertEqual(Move.default_get(['move_type'])['move_type'], 'in_invoice')
        bill = Move.create({
            'partner_id': self.vendor.id, 'invoice_date': fields.Date.today(),
            'invoice_line_ids': [Command.create({'product_id': self.product.id, 'quantity': 2, 'price_unit': 8.0})],
        })
        bill.action_post()
        self.assertEqual((bill.move_type, bill.state), ('in_invoice', 'posted'))
        self.assertFalse(bill.invoice_line_ids.purchase_line_id, "no RFQ or purchase order was needed")

    def test_the_rfq_to_order_to_bill_workflow_still_works(self):
        order = self._order()
        self.assertEqual(order.state, 'draft')
        order.button_confirm()
        self.assertEqual(order.state, 'purchase')
        order.action_create_invoice()
        bill = order.invoice_ids
        self.assertEqual(len(bill), 1)
        self.assertEqual(bill.invoice_line_ids.purchase_line_id, order.order_line)

    def test_view_is_the_report_print_uses_rfq_then_order(self):
        order = self._order()
        self.assertEqual(order.action_nc_view()['url'], '/ncollection/document/view/purchase.order/%d' % order.id)
        self.assertEqual(order._nc_print_report().report_name, 'purchase.report_purchasequotation')
        self.assertEqual(order.action_nc_print()['report_name'], 'purchase.report_purchasequotation')
        order.button_confirm()
        self.assertEqual(order._nc_print_report().report_name, 'purchase.report_purchaseorder')
        self.assertEqual(order.action_nc_print()['report_name'], 'purchase.report_purchaseorder')

    def test_files_and_row_actions(self):
        order = self._order()
        self.env['ir.attachment'].create({'name': 'quote.pdf', 'raw': b'%PDF-1.4 synthetic',
                                          'res_model': 'purchase.order', 'res_id': order.id})
        self.assertEqual(order.nc_attachment_count, 1)
        self.assertEqual([f['name'] for f in order.nc_get_attachments()], ['quote.pdf'])
        for view_ref in ('purchase.purchase_order_kpis_tree', 'purchase.purchase_order_view_tree'):
            with self.subTest(view_ref):
                arch = self.env['purchase.order'].get_views([(self.env.ref(view_ref).id, 'list')])['views']['list'][
                    'arch']
                for needle in ('nc_attachment_count', 'action_nc_view', 'action_nc_edit'):
                    self.assertIn(needle, arch)
        form = self.env['purchase.order'].get_views([(False, 'form')])['views']['form']['arch']
        self.assertIn('action_nc_view', form)


@tagged('post_install', '-at_install')
class TestPurchaseViewServesThePrintedDocument(HttpCase):

    def test_the_view_url_returns_the_document_the_report_engine_prints(self):
        import re

        def words(blob):
            text = blob.decode('utf8', 'replace') if isinstance(blob, bytes) else blob
            text = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', text, flags=re.S)
            return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', text)).strip()

        vendor = self.env['res.partner'].create({'name': 'Synthetic Vendor LLC'})
        product = self.env['product.product'].create({'name': 'Synthetic Part', 'standard_price': 8.0})
        order = self.env['purchase.order'].create({
            'partner_id': vendor.id,
            'order_line': [Command.create({'product_id': product.id, 'product_qty': 4, 'price_unit': 8.0})]})
        self.env.flush_all()
        self.authenticate('admin', 'admin')
        for confirm in (False, True):
            if confirm:
                order.button_confirm()
                self.env.flush_all()
            with self.subTest(state=order.state):
                response = self.url_open(order.action_nc_view()['url'])
                self.assertEqual(response.status_code, 200)
                body, _kind = self.env['ir.actions.report']._render_qweb_pdf(
                    order.action_nc_print()['report_name'], order.ids)
                self.assertIn(order.name, words(response.content))
                self.assertEqual(words(response.content), words(body))


@tagged('post_install', '-at_install')
class TestBillsFollowTheAccountingLicence(TransactionCase):
    """Purchase > Vendor Bills is a door onto Accounting: exactly as licensed as Accounting. Mirror of Sales."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'lic_buyer', 'login': 'lic_buyer', 'group_ids': [Command.set([
                cls.env.ref('base.group_user').id, cls.env.ref('account.group_account_invoice').id,
                cls.env.ref('purchase.group_purchase_user').id])]})
        cls.bills = cls.env.ref('ncollection_purchase_documents.menu_purchase_nc_vendor_bills')
        cls.purchase_root = cls.env.ref('purchase.menu_purchase_root')

    def _plan(self, allowed):
        Config = self.env['ncollection.workspace.config'].sudo()
        (Config.get_config() or Config.create({})).write({'allowed_module_names': allowed})
        self.addCleanup(self.env.registry.clear_cache)

    def _visible(self):
        return self.env['ir.ui.menu'].with_user(self.user)._visible_menu_ids()

    def test_a_plan_with_purchase_licenses_accounting_and_keeps_the_entry_usable(self):
        self._plan('purchase')
        blocked = self.env['ir.ui.menu'].sudo()._ncollection_blocked_module_names()
        self.assertNotIn('account', blocked)
        self.assertIn(self.purchase_root.id, self._visible())
        self.assertIn(self.bills.id, self._visible())
        self.env['account.move'].with_user(self.user).check_access('read')

    def test_a_plan_without_purchase_or_accounting_shows_no_bills_entry(self):
        self._plan('crm')
        self.assertNotIn(self.purchase_root.id, self._visible())
        self.assertNotIn(self.bills.id, self._visible())
        with self.assertRaises(AccessError):
            self.env['account.move'].with_user(self.user).check_access('read')

    def test_the_entry_is_hidden_whenever_accounting_is_blocked_even_if_purchase_is_not(self):
        menus = self.env['ir.ui.menu']
        hidden = menus._ncollection_blocked_menu_ids(blocked_modules={'account'})
        self.assertIn(self.bills.id, hidden)
        self.assertNotIn(self.purchase_root.id, hidden)
        self.assertNotIn(self.env.ref('purchase.menu_purchase_rfq').id, hidden, "RFQs are Purchase's")
        self.assertIn(self.bills.id, menus._ncollection_blocked_menu_ids(blocked_modules={'purchase'}))
        self.assertNotIn(self.bills.id, menus._ncollection_blocked_menu_ids(blocked_modules={'crm'}))


@tagged('post_install', '-at_install')
class TestPurchaseViewEqualsPrintInBothLanguages(HttpCase):

    def test_rfq_and_order_read_the_same_in_view_and_print_in_english_and_arabic(self):
        import re

        def words(blob):
            text = blob.decode('utf8', 'replace') if isinstance(blob, bytes) else blob
            text = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', text, flags=re.S)
            return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', text)).strip()

        self.env['res.lang']._activate_lang('ar_001')
        self.env['ir.module.module']._load_module_terms(['purchase'], ['ar_001'], overwrite=True)
        product = self.env['product.product'].create({'name': 'Synthetic Part', 'standard_price': 8.0})
        for lang in ('en_US', 'ar_001'):
            vendor = self.env['res.partner'].create({'name': 'Synthetic Vendor ' + lang, 'lang': lang})
            order = self.env['purchase.order'].create({
                'partner_id': vendor.id,
                'order_line': [Command.create({'product_id': product.id, 'product_qty': 3, 'price_unit': 8.0})]})
            self.env.flush_all()
            self.authenticate('admin', 'admin')
            for confirm in (False, True):
                if confirm:
                    order.button_confirm()
                    self.env.flush_all()
                with self.subTest(lang=lang, state=order.state):
                    response = self.url_open(order.action_nc_view()['url'])
                    self.assertEqual(response.status_code, 200)
                    name = order.action_nc_print()['report_name']
                    body, _kind = self.env['ir.actions.report']._render_qweb_pdf(name, order.ids)
                    self.assertEqual(words(response.content), words(body))
                    self.assertEqual(bool(re.search('[؀-ۿ]', words(body))), lang == 'ar_001')


@tagged('post_install', '-at_install')
class TestPurchaseDocumentsOwnOnlyTheBillsEntry(TransactionCase):

    def test_the_licence_follow_applies_to_exactly_one_menu(self):
        owned = self.env['ir.model.data'].sudo().search([
            ('model', '=', 'ir.ui.menu'), ('module', '=', 'ncollection_purchase_documents')]).mapped('name')
        self.assertEqual(owned, ['menu_purchase_nc_vendor_bills'])
