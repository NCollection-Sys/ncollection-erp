# -*- coding: utf-8 -*-
import base64

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

PNG = base64.b64encode(
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
    b'\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x9a\xa0\xa0\x00\x00\x00\x00IEND\xaeB`\x82')


@tagged('post_install', '-at_install')
class TestSaleOrderAttachments(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Attachment Customer'})
        cls.order = cls.env['sale.order'].create({'partner_id': cls.partner.id})
        cls.salesman = cls.env['res.users'].create({
            'name': 'Sales A', 'login': 'nc_att_sales',
            'group_ids': [(6, 0, [cls.env.ref('sales_team.group_sale_salesman_all_leads').id])]})
        cls.no_sales = cls.env['res.users'].create({
            'name': 'No sales', 'login': 'nc_att_nosales',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])]})

    def _attach(self, order, name, mimetype, raw=b'x', **extra):
        return self.env['ir.attachment'].create(dict({
            'name': name, 'res_model': 'sale.order', 'res_id': order.id, 'mimetype': mimetype,
            'raw': raw}, **extra))

    def test_zero_attachments(self):
        self.assertEqual(self.order.nc_attachment_count, 0)
        self.assertEqual(self.order.nc_get_attachments(), [])

    def test_one_attachment(self):
        self._attach(self.order, 'quote.pdf', 'application/pdf')
        self.assertEqual(self.order.nc_attachment_count, 1)
        self.assertEqual([a['name'] for a in self.order.nc_get_attachments()], ['quote.pdf'])

    def test_multiple_attachments_keep_their_order_and_types(self):
        self._attach(self.order, 'photo.png', 'image/png', base64.b64decode(PNG))
        self._attach(self.order, 'contract.pdf', 'application/pdf')
        # raw bytes that are plainly not text: where libmagic is present Odoo re-derives the type from the content, and
        # b'x' would come back as text/plain
        self._attach(self.order, 'data.bin', 'application/octet-stream', b'\x00\x01\x02\xfe\xff')  # still listed
        files = self.order.nc_get_attachments()
        self.assertEqual([a['name'] for a in files], ['photo.png', 'contract.pdf', 'data.bin'])
        self.assertEqual([a['mimetype'] for a in files], ['image/png', 'application/pdf', 'application/octet-stream'])
        self.assertEqual(self.order.nc_attachment_count, 3)

    def test_arabic_file_names_round_trip(self):
        self._attach(self.order, 'عرض سعر – شركة النور.pdf', 'application/pdf')
        self.assertEqual(self.order.nc_get_attachments()[0]['name'], 'عرض سعر – شركة النور.pdf')

    def test_metadata_never_carries_the_content(self):
        self._attach(self.order, 'a.pdf', 'application/pdf', b'secret-bytes')
        meta = self.order.nc_get_attachments()[0]
        self.assertNotIn('raw', meta)
        self.assertNotIn('datas', meta)
        self.assertNotIn('secret', str(meta))

    def test_binary_field_attachments_and_other_documents_are_not_counted(self):
        other = self.env['sale.order'].create({'partner_id': self.partner.id})
        self._attach(other, 'other.pdf', 'application/pdf')
        self.env['ir.attachment'].create({
            'name': 'field.png', 'res_model': 'sale.order', 'res_id': self.order.id, 'res_field': 'signature',
            'raw': base64.b64decode(PNG)})
        self.assertEqual(self.order.nc_attachment_count, 0)
        self.assertEqual(self.order.nc_get_attachments(), [])

    def test_count_is_one_grouped_query_for_a_whole_page(self):
        orders = self.order | self.env['sale.order'].create({'partner_id': self.partner.id}) \
            | self.env['sale.order'].create({'partner_id': self.partner.id})
        for order in orders:
            self._attach(order, 'f.pdf', 'application/pdf')
        orders.invalidate_recordset()
        with self.assertQueryCount(__system__=3):
            self.assertEqual(orders.mapped('nc_attachment_count'), [1, 1, 1])

    def test_a_user_without_sales_rights_is_denied(self):
        self._attach(self.order, 'secret.pdf', 'application/pdf')
        with self.assertRaises(AccessError):
            self.order.with_user(self.no_sales).nc_get_attachments()

    def test_company_isolation(self):
        company_b = self.env['res.company'].create({'name': 'Company B attachments'})
        order_b = self.env['sale.order'].with_company(company_b).create({
            'partner_id': self.partner.id, 'company_id': company_b.id})
        self._attach(order_b, 'b-only.pdf', 'application/pdf')
        self.salesman.write({'company_ids': [(6, 0, self.env.company.ids)], 'company_id': self.env.company.id})
        with self.assertRaises(AccessError):
            order_b.with_user(self.salesman).nc_get_attachments()
        # the same user still reads an order of their own company
        self._attach(self.order, 'a.pdf', 'application/pdf')
        self.assertEqual(len(self.order.with_user(self.salesman).nc_get_attachments()), 1)

    def test_a_user_reads_only_what_the_order_allows_never_via_the_viewer_call(self):
        """The viewer call is the record's own access check, not a new door to ir.attachment."""
        self._attach(self.order, 'x.pdf', 'application/pdf')
        with self.assertRaises(AccessError):
            self.order.with_user(self.no_sales).nc_get_attachments()

    def test_no_new_model_route_or_acl(self):
        self.assertFalse(self.env['ir.model'].search([('model', 'like', 'ncollection.sale.attach%')]))
