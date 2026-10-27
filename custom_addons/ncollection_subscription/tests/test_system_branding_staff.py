# -*- coding: utf-8 -*-
"""In the platform database, platform staff change the identity of the system (ncollection_branding).

The other half of the boundary (the tenant Owner cannot) is tested in ncollection_core, where the tenant topology is
simulated. Here the platform topology is real: ``base.group_system`` implies ``Platform Admin`` (unchanged), and
``Platform Admin`` now implies ``System Branding Manager``, so the operators keep the access they had.
"""
from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

MANAGER = 'ncollection_branding.group_system_branding_manager'


@tagged('post_install', '-at_install')
class TestSystemBrandingStaff(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Brand = cls.env['ncollection.product.branding']
        cls.record = cls.Brand._nc_record()
        cls.platform_group = cls.env.ref('ncollection_subscription.group_platform_admin')

    def _user(self, login, *group_xml_ids):
        return self.env['res.users'].with_context(no_reset_password=True).create({
            'name': login, 'login': login,
            'group_ids': [Command.set([self.env.ref(x).id for x in group_xml_ids])]})

    def test_platform_admin_implies_the_system_branding_right(self):
        self.assertIn(self.env.ref(MANAGER), self.platform_group.all_implied_ids)

    def test_a_platform_administrator_changes_the_system_branding(self):
        staff = self._user('platform_staff', 'base.group_user', 'ncollection_subscription.group_platform_admin')
        self.assertTrue(staff.has_group(MANAGER))
        self.record.with_user(staff).write({'copyright_text': '© 2026 Platform Ltd. All rights reserved.'})
        self.assertEqual(self.Brand.nc_brand()['copyright'], '© 2026 Platform Ltd. All rights reserved.')
        self.assertTrue(self.Brand.with_user(staff).action_open())

    def test_the_platform_operators_keep_the_access_they_had_through_administration_settings(self):
        """Existing operators are ``base.group_system`` users; that still leads to the right in this database."""
        operator = self._user('platform_operator', 'base.group_system')
        self.assertTrue(operator.has_group(MANAGER))
        self.record.with_user(operator).write({'browser_title': 'Operator Console'})
        self.assertEqual(self.Brand.nc_brand()['title'], 'Operator Console')

    def test_an_ordinary_user_has_no_part_of_it(self):
        plain = self._user('platform_plain', 'base.group_user')
        self.assertFalse(plain.has_group(MANAGER))
        with self.assertRaises(AccessError):
            self.record.with_user(plain).write({'name': 'Hijacked'})

    def test_a_platform_administrator_may_make_another_operator(self):
        """The grant guard (ncollection_branding) must not lock platform staff out of managing platform staff: making
        someone a Platform Admin or a system administrator here makes them a holder, and a Platform Admin may do so."""
        staff = self._user('platform_granting_staff', 'base.group_system')
        colleague = self.env['res.users'].with_user(staff).with_context(no_reset_password=True).create({
            'name': 'New Operator', 'login': 'platform_new_operator',
            'group_ids': [Command.set([self.env.ref('base.group_user').id, self.platform_group.id])]})
        self.assertTrue(colleague.sudo().has_group(MANAGER))

    def test_a_non_staff_user_manager_cannot_hand_out_the_right_in_the_platform_database(self):
        """``base.group_erp_manager`` can write users and groups in Odoo; without Platform Admin it is not staff."""
        user_admin = self._user('platform_user_admin', 'base.group_user', 'base.group_erp_manager')
        self.assertFalse(user_admin.has_group(MANAGER))
        plain = self._user('platform_target', 'base.group_user')
        with self.assertRaises(AccessError):
            plain.with_user(user_admin).write({'group_ids': [Command.link(self.env.ref(MANAGER).id)]})
        with self.assertRaises(AccessError):
            user_admin.with_user(user_admin).write({'group_ids': [Command.link(self.platform_group.id)]})
        self.assertFalse(plain.has_group(MANAGER))

    def test_the_implication_is_applied_by_an_upgrade_and_not_only_by_a_fresh_install(self):
        """Measured on a copy of the real platform database: Platform Admin is declared in a noupdate block, so Odoo
        skips a later implied_ids line on it when it UPGRADES an existing database (0 rows) while a fresh install
        applies it. The link is therefore written from the manager group's side. This test loads the data file the way
        an upgrade does (mode='update') after taking the link away, and requires it to be back."""
        from odoo.tools import convert_file
        manager = self.env.ref(MANAGER)
        self.platform_group.write({'implied_ids': [Command.unlink(manager.id)]})
        self.assertNotIn(manager, self.platform_group.implied_ids)
        convert_file(self.env, 'ncollection_subscription', 'security/saas_security.xml', {}, mode='update',
                     noupdate=False)
        self.assertIn(manager, self.platform_group.implied_ids, 'an upgrade must restore the platform staff right')
        operator = self._user('platform_upgraded_operator', 'base.group_system')
        self.assertTrue(operator.has_group(MANAGER))
