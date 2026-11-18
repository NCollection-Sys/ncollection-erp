# -*- coding: utf-8 -*-
"""Sales / Purchase / Inventory Settings inside the unified Settings: who may open, see and save each section, and
that a save lands where Odoo keeps the value (reloading shows it). Never base.group_system, never res.config.settings
as the user. Purchase / Inventory reuse this class in their own modules."""
import json

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestSettingsSections(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.env.company
        common = dict(company_id=company.id, company_ids=[(6, 0, company.ids)])
        cls.admin = new_test_user(cls.env, login='sec_admin',
                                  groups='base.group_user,ncollection_core.group_tenant_admin', **common)
        cls.delegate = new_test_user(cls.env, login='sec_delegate',
                                     groups='base.group_user,ncollection_core.group_settings_sales', **common)
        cls.plain = new_test_user(cls.env, login='sec_plain', groups='base.group_user', **common)
        cls.config = cls.env['ncollection.workspace.config'].sudo().search([], limit=1) or \
            cls.env['ncollection.workspace.config'].sudo().create({})

    def _policy(self, **levels):
        self.config.write({'settings_policy': json.dumps(levels) if levels else False})

    def _page(self, user, section, **vals):
        return self.env['ncollection.business.settings'].with_user(user).with_context(
            nc_settings_section=section).create(vals)

    def test_admin_saves_and_reload_shows_it(self):
        self.assertFalse(self.admin.has_group('base.group_system'))
        with self.assertRaises(AccessError):
            self.env['res.config.settings'].with_user(self.admin).create({})
        self._page(self.admin, 'sales', quotation_validity_days=17, group_warning_sale=True,
                   default_invoice_policy='delivery').action_nc_save()
        again = self._page(self.admin, 'sales')
        self.assertEqual(again.quotation_validity_days, 17)
        self.assertTrue(again.group_warning_sale)
        self.assertEqual(again.default_invoice_policy, 'delivery')
        self.assertEqual(self.env['ir.default'].sudo()._get('product.template', 'invoice_policy'), 'delivery')
        self.assertIn(self.env.ref('sale.group_warning_sale'), self.env.ref('base.group_user').implied_ids)

    def test_unchanged_values_are_left_alone(self):
        before = self.env.company.quotation_validity_days
        self._page(self.admin, 'sales', group_warning_sale=True).action_nc_save()
        self.assertEqual(self.env.company.quotation_validity_days, before)

    def test_plain_user_cannot_open(self):
        with self.assertRaises(AccessError):
            self._page(self.plain, 'sales')

    def test_delegate_of_sales_only(self):
        self._page(self.delegate, 'sales', quotation_validity_days=9).action_nc_save()
        self.assertEqual(self.env.company.quotation_validity_days, 9)
        page = self._page(self.delegate, 'sales')
        self.assertEqual(page.nc_level_business, 'hidden')
        self.assertEqual(page.nc_level_sales, 'edit')
        with self.assertRaises(AccessError):
            self._page(self.delegate, 'business')

    def test_delegate_loses_it_when_platform_stops_delegation(self):
        self._policy(sales='edit')
        with self.assertRaises(AccessError):
            self._page(self.delegate, 'sales')

    def test_view_only_is_refused_on_save(self):
        self._policy(sales='view')
        page = self._page(self.admin, 'sales')
        self.assertEqual(page.nc_level_sales, 'view')
        page.quotation_validity_days = page.quotation_validity_days + 5
        with self.assertRaises(AccessError):
            page.action_nc_save()

    def test_hidden_section_is_not_read_nor_written(self):
        self._policy(sales='hidden')
        start = self.env.company.quotation_validity_days
        with self.assertRaises(AccessError):            # its own page (menu, URL, RPC) is refused outright
            self._page(self.admin, 'sales')
        page = self.env['ncollection.business.settings'].with_user(self.admin).create({})   # the all-sections page
        self.assertEqual(page.nc_level_sales, 'hidden')
        page.quotation_validity_days = start + 7
        page.action_nc_save()                          # nothing visible -> nothing written
        self.assertEqual(self.env.company.quotation_validity_days, start)
        menu = self.env.ref('ncollection_sale_documents.menu_workspace_sales_settings')
        self.assertIn(menu.id, self.env['ncollection.settings.policy']._nc_hidden_menu_ids())


@tagged('post_install', '-at_install')
class TestSettingsExecutePath(TestSettingsSections):
    """The Save of Odoo's settings view calls ``execute``: the same guarded save, nothing more."""

    def _page_all(self, user, **vals):
        return self.env['ncollection.business.settings'].with_user(user).create(vals)

    def test_execute_is_the_guarded_save(self):
        result = self._page_all(self.admin, quotation_validity_days=23).execute()
        self.assertEqual(result, {'type': 'ir.actions.client', 'tag': 'reload'})
        self.assertEqual(self.env.company.quotation_validity_days, 23)

    def test_execute_cannot_install_or_remove_a_module(self):
        fields_ = self.env['ncollection.business.settings']._fields
        self.assertFalse([name for name in fields_ if name.startswith('module_')],
                         "no module_* setting exists on the page, so nothing can be installed from it")
        states = dict(self.env['ir.module.module'].sudo().search([]).mapped(lambda m: (m.name, m.state)))
        self._page_all(self.admin, quotation_validity_days=24, group_warning_sale=True).execute()
        after = dict(self.env['ir.module.module'].sudo().search([]).mapped(lambda m: (m.name, m.state)))
        self.assertEqual(states, after)

    def test_execute_respects_hidden_and_view_only(self):
        start = self.env.company.quotation_validity_days
        self._policy(sales='hidden')
        self._page_all(self.admin, quotation_validity_days=start + 9).execute()
        self.assertEqual(self.env.company.quotation_validity_days, start, "hidden: not written")
        self._policy(sales='view')
        with self.assertRaises(AccessError):
            self._page_all(self.admin, quotation_validity_days=start + 9).execute()
        self.assertEqual(self.env.company.quotation_validity_days, start, "view only: refused")

    def test_execute_writes_only_the_current_company(self):
        other = self.env['res.company'].create({'name': 'Settings Other Co'})
        before_other = other.quotation_validity_days
        page = self._page_all(self.admin, quotation_validity_days=31, company_id=other.id)
        page.execute()
        self.assertEqual(self.env.company.quotation_validity_days, 31, "the user's current company")
        self.assertEqual(other.quotation_validity_days, before_other, "a client-sent company is ignored")

    def test_execute_never_grants_technical_rights(self):
        self._page_all(self.admin, group_warning_sale=True, group_uom=True).execute()
        self.admin.invalidate_recordset()
        self.assertFalse(self.admin.has_group('base.group_system'))
        self.assertFalse(self.admin.has_group('base.group_erp_manager'))
        with self.assertRaises(AccessError):
            self._page_all(self.plain, quotation_validity_days=5).execute()
        with self.assertRaises(AccessError):              # a delegate of Sales cannot save another section's value
            self.env['ncollection.business.settings'].with_user(self.delegate).with_context(
                nc_settings_section='purchase').create({}).execute()
