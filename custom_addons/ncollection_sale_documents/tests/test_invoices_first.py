# -*- coding: utf-8 -*-
"""Sales opens on Invoices: New is a customer invoice straight away; quotations / orders stay available. Synthetic."""
from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import HttpCase, TransactionCase, tagged
from odoo.tools.safe_eval import safe_eval


@tagged('post_install', '-at_install')
class TestSalesInvoicesFirst(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.customer = cls.env['res.partner'].create({'name': 'Synthetic Customer LLC'})
        cls.product = cls.env['product.product'].create({'name': 'Synthetic Item', 'list_price': 25.0,
                                                         'invoice_policy': 'order'})
        cls.menu = cls.env.ref('ncollection_sale_documents.menu_sale_nc_invoices')

    def _menu_context(self):
        return safe_eval(self.menu.action.context or '{}', {'uid': self.env.uid, 'context': {}})

    def test_the_sales_app_exists_for_a_tenant_that_has_sale_but_not_sale_management(self):
        """Odoo 19 leaves the `sale` root menu inactive (only sale_management turns it on): without this the new
        Invoices entry would sit under a menu nobody can see."""
        root = self.env.ref('sale.sale_menu_root')
        self.assertTrue(root.active)
        self.assertEqual(self.env['ir.model.data']._xmlid_to_res_id('sale.sale_menu_root'), root.id)
        self.assertEqual(root.web_icon, 'sale,static/description/icon.png')
        self.assertIn(root.id, self.env['ir.ui.menu']._visible_menu_ids(),
                      "visible to a user who holds the Sales and invoicing rights (the test user is the superuser)")

    def test_invoices_is_the_first_sales_entry_and_reuses_the_accounting_action(self):
        root = self.env.ref('sale.sale_menu_root')
        self.assertEqual(self.menu.parent_id, root)
        self.assertEqual(root.child_id.sorted(lambda m: (m.sequence, m.id))[:1], self.menu)
        self.assertEqual(self.menu.action, self.env.ref('account.action_move_out_invoice_type'),
                         "the accounting customer-invoice action itself, not a second implementation")
        self.assertEqual(self.menu.group_ids, self.env.ref('account.group_account_invoice'),
                         "only users who may invoice see it (the ORM right behind it)")
        quotations = self.env.ref('sale.menu_sale_quotations')
        self.assertTrue(quotations.active and self.env.ref('sale.menu_sale_order').active)

    def test_new_from_the_menu_is_a_customer_invoice_with_no_order_behind_it(self):
        Move = self.env['account.move'].with_context(**self._menu_context())
        self.assertEqual(Move.default_get(['move_type'])['move_type'], 'out_invoice')
        invoice = Move.create({
            'partner_id': self.customer.id,
            'invoice_line_ids': [Command.create({'product_id': self.product.id, 'quantity': 2, 'price_unit': 25.0})],
        })
        invoice.action_post()
        self.assertEqual((invoice.move_type, invoice.state), ('out_invoice', 'posted'))
        self.assertFalse(invoice.invoice_line_ids.sale_line_ids, "no quotation or order was needed")

    def test_the_quotation_to_order_to_invoice_workflow_still_works(self):
        order = self.env['sale.order'].create({
            'partner_id': self.customer.id,
            'order_line': [Command.create({'product_id': self.product.id, 'product_uom_qty': 3, 'price_unit': 25.0})]})
        self.assertEqual(order.state, 'draft')
        order.action_confirm()
        invoice = order._create_invoices()
        invoice.action_post()
        self.assertEqual(invoice.invoice_line_ids.sale_line_ids, order.order_line)
        self.assertEqual(invoice.amount_untaxed, 75.0)

    def test_view_and_print_of_a_quotation_are_one_report(self):
        order = self.env['sale.order'].create({
            'partner_id': self.customer.id,
            'order_line': [Command.create({'product_id': self.product.id, 'product_uom_qty': 1, 'price_unit': 25.0})]})
        self.assertEqual(order.action_nc_view()['url'], '/ncollection/document/view/sale.order/%d' % order.id)
        self.assertEqual(order._nc_print_report().report_name, 'sale.report_saleorder')
        self.assertEqual(order.action_nc_print()['report_name'], 'sale.report_saleorder')
        self.assertEqual(order.action_preview_sale_order(), order.action_nc_view())
        lists = self.env['sale.order'].get_views([(False, 'list')])['views']['list']['arch']
        self.assertIn('action_nc_view', lists)
        self.assertIn('action_nc_edit', lists)


@tagged('post_install', '-at_install')
class TestInvoicesFollowTheAccountingLicence(TransactionCase):
    """Sales > Invoices is a door onto Accounting: exactly as licensed as Accounting, never an unusable entry."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'lic_sales', 'login': 'lic_sales', 'group_ids': [Command.set([
                cls.env.ref('base.group_user').id, cls.env.ref('account.group_account_invoice').id,
                cls.env.ref('sales_team.group_sale_salesman').id])]})
        cls.invoices = cls.env.ref('ncollection_sale_documents.menu_sale_nc_invoices')
        cls.sale_root = cls.env.ref('sale.sale_menu_root')

    def _plan(self, allowed):
        """The plan the platform pushed: write it the way config-sync does (which clears the licence caches)."""
        Config = self.env['ncollection.workspace.config'].sudo()
        (Config.get_config() or Config.create({})).write({'allowed_module_names': allowed})
        self.addCleanup(self.env.registry.clear_cache)

    def _visible(self):
        return self.env['ir.ui.menu'].with_user(self.user)._visible_menu_ids()

    def _account_move_readable(self):
        try:
            self.env['account.move'].with_user(self.user).check_access('read')
            return True
        except AccessError:
            return False

    def test_a_plan_with_sales_licenses_accounting_through_the_dependency_closure(self):
        self._plan('sale')
        blocked = self.env['ir.ui.menu'].sudo()._ncollection_blocked_module_names()
        self.assertNotIn('account', blocked, "sale depends on account: licensing one licenses the other")
        self.assertIn(self.sale_root.id, self._visible())
        self.assertIn(self.invoices.id, self._visible(), "Sales > Invoices stays available")
        self.assertTrue(self._account_move_readable(), "and the model behind it is usable")

    def test_a_plan_without_sales_or_accounting_shows_no_invoices_entry(self):
        self._plan('crm')
        blocked = self.env['ir.ui.menu'].sudo()._ncollection_blocked_module_names()
        self.assertTrue({'sale', 'account'} <= set(blocked))
        self.assertNotIn(self.sale_root.id, self._visible())
        self.assertNotIn(self.invoices.id, self._visible())
        self.assertFalse(self._account_move_readable())

    def test_an_accounting_only_plan_has_no_sales_menu_so_no_invoices_entry_there(self):
        self._plan('account')
        self.assertNotIn(self.invoices.id, self._visible())
        self.assertTrue(self._account_move_readable(), "Accounting's own menus and model are unaffected")

    def test_the_entry_is_hidden_whenever_accounting_is_blocked_even_if_sales_is_not(self):
        """The gate itself, not a coincidence of Odoo's dependencies: sale licensed, account NOT."""
        menus = self.env['ir.ui.menu']
        hidden = menus._ncollection_blocked_menu_ids(blocked_modules={'account'})
        self.assertIn(self.invoices.id, hidden)
        self.assertNotIn(self.sale_root.id, hidden, "the Sales app itself stays: quotations and orders are Sales'")
        self.assertNotIn(self.env.ref('sale.menu_sale_quotations').id, hidden,
                         "quotations are Sales', not Accounting's")

    def test_blocking_sales_hides_the_entry_with_its_app_and_blocking_something_else_does_not(self):
        menus = self.env['ir.ui.menu']
        self.assertIn(self.invoices.id, menus._ncollection_blocked_menu_ids(blocked_modules={'sale'}))
        self.assertNotIn(self.invoices.id, menus._ncollection_blocked_menu_ids(blocked_modules={'crm'}))

    def test_the_follow_is_declared_in_the_licence_gate_not_in_the_menu(self):
        from odoo.addons.ncollection_core.models.ir_ui_menu import NATIVE_FOLLOWS_LICENCE
        self.assertEqual(NATIVE_FOLLOWS_LICENCE['ncollection_sale_documents'], 'account')
        self.assertEqual(NATIVE_FOLLOWS_LICENCE['ncollection_purchase_documents'], 'account')
        self.assertEqual(NATIVE_FOLLOWS_LICENCE['ncollection_stock_barcode'], 'stock', "the existing entry is kept")


@tagged('post_install', '-at_install')
class TestOrderViewEqualsPrintInBothLanguages(HttpCase):

    def test_quotation_and_order_read_the_same_in_view_and_print_in_english_and_arabic(self):
        import re

        def words(blob):
            text = blob.decode('utf8', 'replace') if isinstance(blob, bytes) else blob
            text = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', text, flags=re.S)
            return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', text)).strip()

        self.env['res.lang']._activate_lang('ar_001')
        self.env['ir.module.module']._load_module_terms(['sale'], ['ar_001'], overwrite=True)
        product = self.env['product.product'].create({'name': 'Synthetic Item', 'list_price': 25.0})
        for lang in ('en_US', 'ar_001'):
            partner = self.env['res.partner'].create({'name': 'Synthetic Customer ' + lang, 'lang': lang})
            quotation = self.env['sale.order'].create({
                'partner_id': partner.id,
                'order_line': [Command.create({'product_id': product.id, 'product_uom_qty': 2, 'price_unit': 25.0})]})
            order = self.env['sale.order'].create({
                'partner_id': partner.id,
                'order_line': [Command.create({'product_id': product.id, 'product_uom_qty': 1, 'price_unit': 25.0})]})
            order.action_confirm()
            self.env.flush_all()
            self.authenticate('admin', 'admin')
            for record in (quotation, order):
                with self.subTest(lang=lang, state=record.state):
                    response = self.url_open(record.action_nc_view()['url'])
                    self.assertEqual(response.status_code, 200)
                    report = record._nc_print_report()
                    self.assertEqual(record.action_nc_print()['report_name'], report.report_name)
                    body, _kind = self.env['ir.actions.report']._render_qweb_pdf(report.report_name, record.ids)
                    self.assertEqual(words(response.content), words(body))
                    self.assertEqual(bool(re.search('[؀-ۿ]', words(body))), lang == 'ar_001')


@tagged('post_install', '-at_install')
class TestSalesDocumentsOwnOnlyTheInvoicesEntry(TransactionCase):

    def test_the_licence_follow_applies_to_exactly_one_menu(self):
        """NATIVE_FOLLOWS_LICENCE hides every menu this module owns when Accounting is blocked: keep that to the one
        entry that is an Accounting door (the Sales root stays owned by `sale`)."""
        owned = self.env['ir.model.data'].sudo().search([
            ('model', '=', 'ir.ui.menu'), ('module', '=', 'ncollection_sale_documents')]).mapped('name')
        self.assertEqual(owned, ['menu_sale_nc_invoices'])
