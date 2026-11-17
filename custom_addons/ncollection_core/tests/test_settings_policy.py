# -*- coding: utf-8 -*-
"""Unified Settings: the platform's levels are enforced at the ORM, delegation is group membership, and a tenant can
only narrow what the platform pushed. Real write paths (res.company delegation, roles, users, labels), real users."""
import json

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestSettingsPolicy(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.env['ncollection.workspace.config'].search([], limit=1) or \
            cls.env['ncollection.workspace.config'].create({})
        cls.admin = new_test_user(cls.env, login='nc_sp_admin', company_id=cls.env.company.id,
                                  groups='base.group_user,ncollection_core.group_tenant_admin')
        cls.colleague = new_test_user(cls.env, login='nc_sp_colleague', company_id=cls.env.company.id,
                                      groups='base.group_user')
        cls.Policy = cls.env['ncollection.settings.policy']

    def _push(self, **levels):
        self.config.sudo().write({'settings_policy': json.dumps(levels) if levels else False})

    def test_without_a_pushed_policy_everything_stays_as_before(self):
        self._push()
        self.assertEqual(set(self.Policy._nc_platform_levels().values()), {'administer'})
        self.assertEqual(self.Policy.with_user(self.admin)._nc_level('company'), 'administer')
        self.env.company.with_user(self.admin).write({'phone': '+971 4 000 0000'})

    def test_view_only_refuses_the_write_server_side(self):
        self._push(company='view', appearance='edit')
        with self.assertRaises(AccessError):
            self.env.company.with_user(self.admin).write({'phone': '+971 4 111 1111'})
        self.env.company.with_user(self.admin).write({'nc_primary_color': '#123456'})  # another section, allowed

    def test_hidden_section_has_no_menu_and_no_write(self):
        self._push(interface_text='hidden', company='administer')
        menu = self.env.ref('ncollection_core.menu_workspace_interface_text')
        self.env.registry.clear_cache()
        self.assertNotIn(menu.id, self.env['ir.ui.menu'].with_user(self.admin)._visible_menu_ids())
        field = self.env['ir.model.fields']._get('res.partner', 'ref')
        with self.assertRaises(AccessError):
            self.env['ncollection.label.override'].with_user(self.admin).create(
                {'target_type': 'field', 'field_id': field.id, 'lang': 'en_US', 'value': 'Code'})

    def test_roles_and_users_follow_their_sections(self):
        self._push(roles='view', users='view')
        with self.assertRaises(AccessError):
            self.env['ncollection.role'].with_user(self.admin).create({'name': 'Auditors'})
        with self.assertRaises(AccessError):
            self.colleague.with_user(self.admin).nc_update_profile(name='Renamed')

    def test_delegation_is_group_membership_and_only_where_allowed(self):
        self._push(company='administer', business='edit')
        Delegation = self.env['ncollection.settings.delegation'].with_user(self.admin)
        wizard = Delegation.browse(Delegation.action_open()['res_id'])
        line = wizard.line_ids.filtered(lambda ln: ln.section == 'company')
        self.assertTrue(line.allowed)
        line.user_ids = self.colleague
        wizard.action_save()
        self.assertTrue(self.colleague.has_group('ncollection_core.group_settings_company'))
        self.assertEqual(self.Policy.with_user(self.colleague)._nc_level('company'), 'edit')
        self.assertEqual(self.Policy.with_user(self.colleague)._nc_level('users'), 'hidden')
        self.env.company.with_user(self.colleague).write({'phone': '+971 4 222 2222'})
        with self.assertRaises(AccessError):
            self.env.company.with_user(self.colleague).write({'nc_primary_color': '#654321'})  # not delegated
        wizard = Delegation.browse(Delegation.action_open()['res_id'])
        business = wizard.line_ids.filtered(lambda ln: ln.section == 'business')
        self.assertFalse(business.allowed, "the platform said edit, not administer")
        business.user_ids = self.colleague
        with self.assertRaises(AccessError):
            wizard.action_save()

    def test_an_ordinary_user_cannot_delegate_or_widen_the_policy(self):
        with self.assertRaises(AccessError):
            self.env['ncollection.settings.delegation'].with_user(self.colleague).action_open()
        with self.assertRaises(AccessError):
            self.config.with_user(self.admin).write({'settings_policy': json.dumps({'company': 'administer'})})

    def test_unknown_section_or_level_is_refused(self):
        self._push(company='superuser')
        self.assertEqual(self.Policy._nc_platform_levels()['company'], 'hidden', "fails closed")
        with self.assertRaises(Exception):
            self.Policy._nc_level('nonexistent')
        self.assertTrue(ValidationError)
