# -*- coding: utf-8 -*-
"""DUP-01 on Contacts: Duplicate is the model's own copy(), as the calling user, never touching the original and never
carrying a legal identity."""
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestDuplicateContacts(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = new_test_user(cls.env, login='dup_manager', groups='base.group_user,base.group_partner_manager')
        cls.reader = new_test_user(cls.env, login='dup_reader', groups='base.group_user')
        cls.partner = cls.env['res.partner'].create({
            'name': 'Duplicate Source LLC', 'vat': 'AE100000000000003', 'company_registry': 'CR-777',
            'email': 'src@example.com', 'phone': '+971 4 000 0000', 'street': '1 Source Road'})

    def test_a_duplicate_is_a_new_contact_without_the_legal_identity(self):
        before = self.partner.read(['name', 'vat', 'company_registry', 'email', 'street', 'write_date'])
        action = self.partner.with_user(self.manager).action_nc_duplicate()
        new = self.env['res.partner'].browse(action['res_id'])
        self.assertEqual((action['res_model'], action['view_mode']), ('res.partner', 'form'))
        self.assertNotEqual(new, self.partner)
        self.assertEqual((new.email, new.street, new.phone),
                         (self.partner.email, self.partner.street, self.partner.phone))
        self.assertFalse(new.vat, "a tax number exists once")
        self.assertFalse(new.company_registry, "a registration exists once")
        self.assertIn('Duplicate Source LLC', new.name)
        after = self.partner.read(['name', 'vat', 'company_registry', 'email', 'street', 'write_date'])
        self.assertEqual(after, before, "the original is untouched")

    def test_a_user_who_may_not_create_contacts_gets_odoos_own_refusal(self):
        count = self.env['res.partner'].search_count([])
        with self.assertRaises(AccessError):
            self.partner.with_user(self.reader).action_nc_duplicate()
        self.assertEqual(self.env['res.partner'].search_count([]), count, "nothing was created behind their back")

    def test_the_company_boundary_still_applies(self):
        other = self.env['res.company'].create({'name': 'Dup Other Co'})
        foreign = self.env['res.partner'].create({'name': 'Foreign', 'company_id': other.id})
        with self.assertRaises(AccessError):
            foreign.with_user(self.manager).action_nc_duplicate()
