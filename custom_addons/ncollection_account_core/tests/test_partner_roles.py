# -*- coding: utf-8 -*-
"""Customer / Vendor tags: read from the partner's own classification, never stored, never in the plain name."""
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestPartnerRoles(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Partner = cls.env['res.partner']
        cls.customer = Partner.create({'name': 'Roletest Customer', 'customer_rank': 1})
        cls.vendor = Partner.create({'name': 'Roletest Vendor', 'supplier_rank': 2})
        cls.both = Partner.create({'name': 'Roletest Both', 'customer_rank': 1, 'supplier_rank': 1})
        cls.neither = Partner.create({'name': 'Roletest Neither'})
        cls.env['res.lang']._activate_lang('ar_001')
        cls.env['ir.module.module']._load_module_terms(['ncollection_account_core'], ['ar_001'], overwrite=True)

    def _formatted(self, partner, lang='en_US'):
        """The dropdown line of a document that opted in (nc.partner.roles.mixin)."""
        return partner.with_context(lang=lang, formatted_display_name=True, nc_partner_roles=True).display_name

    def test_the_flags_follow_the_partners_own_classification(self):
        self.assertEqual((self.customer.nc_is_customer, self.customer.nc_is_vendor), (True, False))
        self.assertEqual((self.vendor.nc_is_customer, self.vendor.nc_is_vendor), (False, True))
        self.assertEqual((self.both.nc_is_customer, self.both.nc_is_vendor), (True, True))
        self.assertEqual((self.neither.nc_is_customer, self.neither.nc_is_vendor), (False, False))

    def test_a_contact_takes_the_role_of_its_company(self):
        contact = self.env['res.partner'].create({'name': 'Roletest Contact', 'parent_id': self.customer.id})
        self.assertEqual(contact.customer_rank, 0)
        self.assertTrue(contact.nc_is_customer)
        self.assertFalse(contact.nc_is_vendor)

    def test_the_dropdown_line_carries_the_tags(self):
        self.assertIn('`Customer`', self._formatted(self.customer))
        self.assertNotIn('`Vendor`', self._formatted(self.customer))
        self.assertIn('`Vendor`', self._formatted(self.vendor))
        self.assertNotIn('`Customer`', self._formatted(self.vendor))
        both = self._formatted(self.both)
        self.assertIn('`Customer`', both)
        self.assertIn('`Vendor`', both)

    def test_neither_gets_no_tag_and_the_name_is_unchanged(self):
        formatted = self._formatted(self.neither)
        self.assertNotIn('`', formatted)
        self.assertTrue(formatted.startswith('Roletest Neither'))

    def test_the_plain_display_name_is_never_touched(self):
        for partner in (self.customer, self.vendor, self.both, self.neither):
            self.assertNotIn('`', partner.display_name)
            self.assertNotIn('Customer', partner.display_name.replace('Roletest Customer', ''))
            self.assertEqual(partner.display_name, partner.name)

    def test_the_tags_follow_the_language(self):
        arabic = self._formatted(self.both, 'ar_001')
        self.assertIn('`عميل`', arabic)
        self.assertIn('`مورد`', arabic)
        self.assertNotIn('Customer', arabic)
        self.assertIn('`Customer`', self._formatted(self.both, 'en_US'))

    def test_the_autocomplete_call_returns_the_tagged_line(self):
        result = self.env['res.partner'].with_context(nc_partner_roles=True).web_name_search(
            'Roletest', {'display_name': {}})
        by_name = {row['display_name']: row['__formatted_display_name'] for row in result}
        self.assertIn('`Customer`', by_name['Roletest Customer'])
        self.assertIn('`Vendor`', by_name['Roletest Vendor'])
        self.assertNotIn('`', by_name['Roletest Neither'])
        arabic = self.env['res.partner'].with_context(lang='ar_001', nc_partner_roles=True).web_name_search(
            'Roletest', {'display_name': {}})
        by_name_ar = {r['display_name']: r['__formatted_display_name'] for r in arabic}
        self.assertIn('`عميل`', by_name_ar['Roletest Customer'])

    def test_showing_the_tags_changes_nothing_and_needs_no_write_right(self):
        reader = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'role_reader', 'login': 'role_reader', 'company_id': self.env.company.id,
            'company_ids': [(6, 0, self.env.company.ids)],
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        before = (self.both.write_date, self.both.customer_rank, self.both.supplier_rank)
        partner = self.both.with_user(reader)
        self.assertTrue(partner.nc_is_customer and partner.nc_is_vendor)
        self.assertIn('`Customer`', partner.with_context(
            formatted_display_name=True, nc_partner_roles=True).display_name)
        self.env.flush_all()
        self.assertEqual((self.both.write_date, self.both.customer_rank, self.both.supplier_rank), before)

    def test_a_posted_customer_invoice_makes_the_partner_a_customer(self):
        partner = self.env['res.partner'].create({'name': 'Roletest Fresh'})
        self.assertFalse(partner.nc_is_customer)
        self.init_invoice('out_invoice', partner=partner, post=True, amounts=[10.0], invoice_date='2026-03-01')
        self.assertTrue(partner.nc_is_customer)
        self.assertFalse(partner.nc_is_vendor)

    def test_the_document_form_shows_the_badges_beside_the_partner(self):
        arch = self.env['account.move'].get_views([(False, 'form')])['views']['form']['arch']
        self.assertIn('o_nc_partner_roles', arch)
        self.assertIn('nc_partner_is_customer', arch)
        arabic = self.env['account.move'].with_context(lang='ar_001').get_views(
            [(False, 'form')])['views']['form']['arch']
        self.assertIn('عميل', arabic)
        self.assertIn('مورد', arabic)

    # ------------------------------------------------------------------ where the badges appear: an opt-in
    def test_a_dropdown_that_did_not_opt_in_shows_the_plain_name(self):
        """CRM, Helpdesk, contacts...: the autocomplete runs with formatted_display_name but without the opt-in."""
        plain = self.both.with_context(formatted_display_name=True).display_name
        self.assertNotIn('`', plain)
        result = self.env['res.partner'].web_name_search('Roletest', {'display_name': {}})
        self.assertFalse([r for r in result if '`' in r['__formatted_display_name']])
        self.assertIn('`Customer`', self._formatted(self.both), "the same partner does get them where opted in")

    def _partner_contexts(self, model):
        from lxml import etree
        # sudo: reading another app's form ARCH only (this test user may not hold, e.g., the CRM group)
        arch = etree.fromstring(self.env[model].sudo().get_views([(False, 'form')])['views']['form']['arch'])
        return [node.get('context') or '' for node in arch.xpath("//field[@name='partner_id']")]

    def test_the_accounting_documents_opt_in_and_keep_their_own_partner_context(self):
        for model in ('account.move', 'account.payment'):
            with self.subTest(model):
                contexts = self._partner_contexts(model)
                self.assertTrue(contexts)
                self.assertTrue(all('nc_partner_roles' in c for c in contexts), contexts)
        move_ctx = self._partner_contexts('account.move')[0]
        self.assertIn('res_partner_search_mode', move_ctx, "Odoo's own context on the field is kept, not replaced")

    def test_an_unrelated_app_does_not_opt_in(self):
        for model in ('crm.lead', 'helpdesk.ticket', 'res.partner'):
            if model not in self.env:
                continue                    # that app is not installed here
            with self.subTest(model):
                self.assertFalse([c for c in self._partner_contexts(model) if 'nc_partner_roles' in c])

    def test_the_context_key_is_merged_into_any_dict_literal(self):
        from odoo.addons.ncollection_core.models.partner_roles_mixin import _with_roles_key
        self.assertEqual(_with_roles_key(None), "{'nc_partner_roles': True}")
        self.assertEqual(_with_roles_key("{'a': 1}"), "{'a': 1, 'nc_partner_roles': True}")
        self.assertEqual(_with_roles_key("{'a': 1,}"), "{'a': 1, 'nc_partner_roles': True}")
        self.assertEqual(_with_roles_key('{}'), "{'nc_partner_roles': True}")
        self.assertIsNone(_with_roles_key('dict(a=1)'), "an expression it does not understand is left alone")

    def test_the_document_flags_read_through_the_partner(self):
        invoice = self.init_invoice('in_invoice', partner=self.both, post=False, amounts=[10.0],
                                    invoice_date='2026-03-01')
        self.assertEqual((invoice.nc_partner_is_customer, invoice.nc_partner_is_vendor), (True, True))
