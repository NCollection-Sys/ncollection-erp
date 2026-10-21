# -*- coding: utf-8 -*-
"""Quotation / order: Duplicate, and Preview = Print. Synthetic data only."""
import re
from datetime import date

from odoo.exceptions import AccessError
from odoo.tests import HttpCase, TransactionCase, tagged


def _words(blob):
    text = blob.decode('utf8', 'replace') if isinstance(blob, bytes) else blob
    text = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', text, flags=re.S)
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', text)).strip()


@tagged('post_install', '-at_install')
class TestSaleDuplicate(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Synthetic Customer LLC'})
        cls.product = cls.env['product.product'].create({'name': 'Synthetic Item', 'list_price': 25.0})
        cls.salesman = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'sd_sales', 'login': 'sd_sales',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('sales_team.group_sale_salesman').id])]})
        cls.stranger = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'sd_none', 'login': 'sd_none', 'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])]})

    def _order(self, confirm=False):
        order = self.env['sale.order'].with_user(self.salesman).create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 3.0, 'price_unit': 25.0})]})
        if confirm:
            order.action_confirm()
        return order

    def _duplicate(self, order, user=None):
        action = order.with_user(user or self.salesman).action_nc_duplicate()
        return self.env['sale.order'].browse(action['res_id'])

    def test_the_order_form_opts_in_to_the_partner_role_badges(self):
        from lxml import etree
        arch = etree.fromstring(self.env['sale.order'].get_views([(False, 'form')])['views']['form']['arch'])
        contexts = [n.get('context') or '' for n in arch.xpath("//field[@name='partner_id']")]
        self.assertTrue(contexts)
        self.assertTrue(all('nc_partner_roles' in c for c in contexts), contexts)

    def test_a_quotation_duplicates_into_a_new_quotation(self):
        original = self._order()
        before = (original.state, original.name, original.amount_total, original.write_date)
        new = self._duplicate(original)
        self.assertNotEqual(new, original)
        self.assertEqual(new.state, 'draft')
        self.assertNotEqual(new.name, original.name, "the quotation number is not reused")
        self.assertEqual(new.partner_id, original.partner_id)
        self.assertEqual(new.order_line.mapped('product_uom_qty'), [3.0])
        self.assertEqual(new.amount_total, original.amount_total)
        self.assertEqual((original.state, original.name, original.amount_total, original.write_date), before)

    def test_a_confirmed_order_duplicates_into_a_draft_with_no_downstream_documents(self):
        original = self._order(confirm=True)
        self.assertEqual(original.state, 'sale')
        new = self._duplicate(original)
        self.assertEqual(new.state, 'draft')
        self.assertFalse(new.invoice_ids)
        self.assertEqual(new.order_line.mapped('qty_invoiced'), [0.0])
        self.assertEqual(new.order_line.mapped('qty_delivered'), [0.0])
        self.assertEqual(original.state, 'sale', "the original stays confirmed")

    def test_a_cancelled_quotation_duplicates_into_a_draft(self):
        original = self._order()
        original._action_cancel()
        self.assertEqual(original.state, 'cancel')
        self.assertEqual(self._duplicate(original).state, 'draft')

    def test_duplicates_get_distinct_numbers(self):
        original = self._order()
        self.assertEqual(len({original.name, self._duplicate(original).name, self._duplicate(original).name}), 3)

    def test_a_user_who_cannot_create_gets_no_copy(self):
        original = self._order()
        count = self.env['sale.order'].search_count([])
        with self.assertRaises(AccessError):
            original.with_user(self.stranger).action_nc_duplicate()
        self.assertEqual(self.env['sale.order'].search_count([]), count)

    def test_the_action_is_not_a_sudo(self):
        self.assertEqual(self._duplicate(self._order(), self.salesman).create_uid, self.salesman)

    def test_the_button_is_in_the_header_and_translated(self):
        arch = self.env['sale.order'].get_views([(False, 'form')])['views']['form']['arch']
        self.assertIn('name="action_nc_duplicate"', arch)
        self.env['res.lang']._activate_lang('ar_001')
        self.env['ir.module.module']._load_module_terms(['ncollection_sale_documents'], ['ar_001'], overwrite=True)
        arabic = self.env['sale.order'].with_context(lang='ar_001').get_views(
            [(False, 'form')])['views']['form']['arch']
        self.assertIn('نسخة مماثلة', arabic)


@tagged('post_install', '-at_install')
class TestSalePreviewIsPrint(HttpCase):

    def test_preview_opens_the_report_print_uses_and_it_renders_the_same_document(self):
        partner = self.env['res.partner'].create({'name': 'Synthetic Customer LLC'})
        product = self.env['product.product'].create({'name': 'Synthetic Item', 'list_price': 25.0})
        order = self.env['sale.order'].create({
            'partner_id': partner.id,
            'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': 1.0, 'price_unit': 25.0})]})
        report = self.env.ref('sale.action_report_saleorder')
        action = order.action_preview_sale_order()
        self.assertEqual(action['url'], '/ncollection/document/view/sale.order/%d' % order.id)
        self.assertEqual(order._nc_print_report(), report, 'View renders the report Print uses')
        self.assertNotIn('/my/', action['url'], "not the customer-portal page")
        self.authenticate('admin', 'admin')
        response = self.url_open(action['url'])
        self.assertEqual(response.status_code, 200)
        body, _kind = report._render_qweb_pdf(report.report_name, order.ids)
        self.assertIn(order.name, _words(response.content))
        self.assertEqual(_words(response.content), _words(body))


@tagged('post_install', '-at_install')
class TestSummaryTopCustomersPeriod(TransactionCase):
    """Top Customers on the Summary is a period total: confirmed orders dated within [From, To]."""

    def test_only_orders_dated_inside_the_period_count(self):
        owner = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'tc_owner', 'login': 'tc_owner',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('ncollection_core.group_role_owner').id,
                                  self.env.ref('sales_team.group_sale_manager').id])]})
        product = self.env['product.product'].create({'name': 'Synthetic Item', 'list_price': 10.0})
        early = self.env['res.partner'].create({'name': 'Synthetic Early Customer'})
        late = self.env['res.partner'].create({'name': 'Synthetic Late Customer'})
        year = date.today().year
        totals = {}
        for partner, when, qty in ((early, date(year, 1, 10), 5.0), (late, date(year, 1, 25), 2.0)):
            order = self.env['sale.order'].create({
                'partner_id': partner.id, 'date_order': when,
                'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': qty, 'price_unit': 10.0})]})
            order.action_confirm()
            order.date_order = when                    # confirming stamps "now": the figure reads the order date
            totals[partner.name] = order.amount_total
        service = self.env['ncollection.dashboard.data'].with_user(owner)

        def top(date_from=None, date_to=None):
            widgets = {w['key']: w for w in service.get_dashboard_payload(date_from, date_to)['widgets']}
            return dict(zip(widgets['top_customers']['labels'], widgets['top_customers']['data']))

        both = top(str(date(year, 1, 1)), str(date(year, 1, 31)))
        self.assertEqual(both.get('Synthetic Early Customer'), totals['Synthetic Early Customer'])
        self.assertEqual(both.get('Synthetic Late Customer'), totals['Synthetic Late Customer'])
        only_late = top(str(date(year, 1, 20)), str(date(year, 1, 31)))
        self.assertNotIn('Synthetic Early Customer', only_late)
        self.assertEqual(only_late.get('Synthetic Late Customer'), totals['Synthetic Late Customer'])
        edge = top(str(date(year, 1, 25)), str(date(year, 1, 25)))
        self.assertEqual(edge.get('Synthetic Late Customer'), totals['Synthetic Late Customer'],
                         "the To date is inclusive")
