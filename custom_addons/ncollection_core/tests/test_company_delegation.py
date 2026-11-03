# -*- coding: utf-8 -*-
"""Option A: the workspace Owner saves their own company (Company, Appearance screens) through res.company's narrow
Workspace Administrator delegation - an explicit field allowlist, own companies only - while a raw technical write and
anything outside the list stay refused."""
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestCompanyDelegation(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        common = {'company_id': cls.company.id, 'company_ids': [(6, 0, cls.company.ids)]}
        cls.owner = new_test_user(cls.env, login='cd_owner',
                                  groups='base.group_user,ncollection_core.group_role_owner',
                                  **common)
        cls.clerk = new_test_user(cls.env, login='cd_clerk',
                                  groups='base.group_user', **common)
        cls.other = cls.env['res.company'].create({'name': 'Delegation Other Co'})

    def test_the_owner_saves_company_identity_and_appearance(self):
        self.company.with_user(self.owner).write({
            'name': 'Delegated Trading LLC', 'street': '1 Creek Road', 'city': 'Dubai', 'phone': '+971 4 000 0000',
            'email': 'office@example.com', 'website': 'https://example.com', 'nc_primary_color': '#123456'})
        self.company.invalidate_recordset()
        self.assertEqual(self.company.name, 'Delegated Trading LLC')
        self.assertEqual(self.company.city, 'Dubai')
        self.assertEqual(self.company.nc_primary_color, '#123456')

    def test_a_field_outside_the_list_is_not_delegated(self):
        for vals in ({'currency_id': self.env.ref('base.EUR').id}, {'name': 'Mixed', 'sequence': 99}):
            with self.subTest(vals=list(vals)), self.assertRaises(AccessError):
                self.company.with_user(self.owner).write(vals)
        # the company hierarchy: not delegated either, and Odoo itself refuses it before the access check
        with self.assertRaises(UserError):
            self.company.with_user(self.owner).write({'parent_id': self.other.id})

    def test_a_company_the_owner_does_not_belong_to_is_refused(self):
        with self.assertRaises(AccessError):
            self.other.with_user(self.owner).write({'name': 'Hijacked'})
        self.assertEqual(self.other.name, 'Delegation Other Co')

    def test_a_user_who_is_not_a_workspace_administrator_is_refused(self):
        with self.assertRaises(AccessError):
            self.company.with_user(self.clerk).write({'name': 'Clerk Renamed'})

    def test_the_owner_still_cannot_reach_native_general_settings(self):
        with self.assertRaises(AccessError):
            self.env['res.config.settings'].with_user(self.owner).create({})

    def test_the_change_is_audited_when_the_audit_log_is_installed(self):
        log = self.env.get('ncollection.auth.log')
        if log is None:
            return      # ncollection_auth not installed: _nc_audit is a no-op by design
        self.company.with_user(self.owner).write({'phone': '+971 4 111 1111'})
        self.assertTrue(log.sudo().search_count([
            ('event_type', '=', 'admin_settings_change'), ('user_id', '=', self.owner.id)]))
