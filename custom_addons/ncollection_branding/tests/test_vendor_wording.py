# -*- coding: utf-8 -*-
"""No vendor product name or vendor link in what a user or a customer is shown, in the data modules ship.

The product name comes from System Branding. Technical references (the `/odoo/...` URL prefix, model names, the
`odoo` package) are not wording and are not asserted against.
"""
import os
import re

from odoo.tests import TransactionCase, tagged

_VENDOR_LINK = re.compile(r'href\s*=\s*["\'][^"\']*odoo\.com', re.I)
# views whose vendor wording is replaced by an inheritance (each is skipped when its module is not installed)
_VIEWS = (
    'auth_signup.reset_password_email', 'portal.portal_record_sidebar', 'portal.portal_my_security',
    'auth_totp_portal.totp_portal_hook', 'auth_totp.auth_totp_form', 'auth_totp_mail.auth_totp_mail_form',
    'auth_totp_mail.account_security_alert', 'digest.digest_mail_main', 'digest.digest_section_mobile',
    'sale.sale_order_portal_template', 'purchase.portal_my_purchase_order',
)


@tagged('post_install', '-at_install')
class TestVendorWording(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.content = cls.env['ncollection.vendor.content']

    def test_no_module_mail_template_names_or_links_the_vendor(self):
        offenders = []
        for template in self.content._nc_module_records('mail.template'):
            for field in ('body_html', 'subject'):
                text = template[field] or ''
                if _VENDOR_LINK.search(text) or re.search(
                        r'Powered by\s*<a|Welcome to Odoo|connect on Odoo|Never heard of Odoo|Enjoy Odoo|'
                        r'connect to Odoo|your Odoo (domain|account)|alt="Odoo"', text):
                    offenders.append('%s.%s' % (template.display_name, field))
        self.assertFalse(offenders, 'vendor wording in module mail templates: %s' % offenders)

    def test_no_module_action_help_links_the_vendor(self):
        offenders = [action.display_name for action in self.content._nc_module_records('ir.actions.act_window')
                     if _VENDOR_LINK.search(action.help or '')]
        self.assertFalse(offenders, 'vendor links in action help: %s' % offenders)

    def test_the_effective_view_of_each_branded_page_has_no_vendor_link(self):
        checked = 0
        for xmlid in _VIEWS:
            view = self.env.ref(xmlid, raise_if_not_found=False)
            if not view:
                continue
            checked += 1
            arch = str(view.sudo().get_combined_arch())
            self.assertIsNone(_VENDOR_LINK.search(arch), '%s still links the vendor' % xmlid)
        self.assertTrue(checked, 'none of the branded pages is installed: the test proves nothing')

    def test_names_of_shipped_groups_and_digests_do_not_say_the_vendor(self):
        for model in ('res.groups', 'digest.digest'):
            if model not in self.env:
                continue
            for record in self.content._nc_module_records(model):
                self.assertFalse(re.search(r'\bOdoo\b', record.name or ''), '%s %s' % (model, record.name))

    def test_applying_it_twice_changes_nothing(self):
        self.content._nc_unbrand()
        self.assertEqual(self.content._nc_unbrand(), 0)

    def test_a_template_a_user_wrote_is_never_rewritten(self):
        mine = self.env['mail.template'].create({
            'name': 'My own wording', 'model_id': self.env.ref('base.model_res_partner').id,
            'subject': 'Hello', 'body_html': '<p>Powered by <a href="https://www.odoo.com">Odoo</a></p>'})
        self.content._nc_unbrand()
        self.assertIn('odoo.com', mine.body_html)

    def test_the_system_user_is_named_after_the_product_and_follows_it(self):
        partner = self.env.ref('base.partner_root')
        brand = self.env['ncollection.product.branding']._nc_record()
        self.assertEqual(partner.name, '%s Bot' % brand.short_name)
        old = brand.short_name
        brand.write({'short_name': 'Acme'})
        self.assertEqual(partner.name, 'Acme Bot')
        brand.write({'short_name': old})
        self.assertEqual(partner.name, '%s Bot' % old)

    def test_a_name_an_administrator_chose_for_it_is_kept(self):
        partner = self.env.ref('base.partner_root')
        partner.write({'name': 'Support Robot'})
        self.env['ncollection.product.branding']._nc_record().write({'short_name': 'Acme'})
        self.assertEqual(partner.name, 'Support Robot')

    def test_the_two_title_parameters_are_not_read_by_any_product_code(self):
        """`web.base.title` and `database.show_name` hold the default product name as data, but nothing in the
        product or in the web client reads them: the tab title and every page come from System Branding. This keeps
        it that way (a reader would show a name that does not follow the configured one)."""
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        offenders = []
        for folder, _dirs, files in os.walk(root):
            if 'node_modules' in folder or os.sep + 'tests' in folder:
                continue
            for name in files:
                if not name.endswith(('.py', '.js', '.xml')) or name == 'system_parameters.xml':
                    continue
                path = os.path.join(folder, name)
                with open(path, encoding='utf-8', errors='ignore') as handle:
                    text = handle.read()
                if 'web.base.title' in text or 'database.show_name' in text:
                    offenders.append(os.path.relpath(path, root))
        self.assertFalse(offenders, 'these now read a parameter that does not follow System Branding: %s' % offenders)

    def test_a_product_name_that_contains_the_vendor_word_never_makes_a_name_grow(self):
        brand = self.env['ncollection.product.branding']._nc_record()
        old = brand.name
        group = self.env['res.groups'].sudo().search([('name', 'ilike', 'Receive notifications')], limit=1)
        group.write({'name': 'Receive notifications in Odoo'})
        before = group.name
        brand.write({'name': 'Odoo Gulf ERP'})
        self.content._nc_unbrand()
        self.content._nc_unbrand()
        self.assertEqual(group.name, before, 'nothing is rewritten while the product name itself says the vendor word')
        brand.write({'name': old})
