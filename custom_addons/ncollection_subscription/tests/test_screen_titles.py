# -*- coding: utf-8 -*-
"""No full-page screen shows a technical title in its breadcrumb.

A window action that opens a FORM on a fresh record in the main area (``target`` current, no ``res_id``) puts that
record's ``display_name`` in the breadcrumb. A model with no name of its own gets Odoo's default display name,
``"<model>,<id>"`` - for a fresh record ``ncollection.subscription.dashboard,NewId_0x76936140a380``, which is exactly
what the SaaS Summary showed under its menu. The fix belongs to the model (its display name), so this sweeps EVERY
such action of every installed NCollection module rather than pinning one page: the next screen-model added without
a title fails here, in English and in Arabic.
"""
import re

from odoo.tests import TransactionCase, tagged

_TECHNICAL = re.compile(r'NewId|^[a-z0-9_]+(\.[a-z0-9_]+)+,')


@tagged('post_install', '-at_install')
class TestScreenTitles(TransactionCase):

    def _full_page_new_record_actions(self):
        # EVERY installed model, Odoo's own included: on the real platform Inventory's "Adjust Valuation"
        # (product.value) and "My Dashboard" (board.board) showed "<model>,NewId_..." exactly like the SaaS Summary
        actions = self.env['ir.actions.act_window'].sudo().search([('target', 'in', ('current', False))])
        return [action for action in actions
                if action.res_model in self.env and not action.res_id
                and (action.view_mode or '').split(',')[0] == 'form']

    def test_every_full_page_form_on_a_new_record_has_a_human_title(self):
        actions = self._full_page_new_record_actions()
        self.assertIn('ncollection.subscription.dashboard', [a.res_model for a in actions],
                      'the sweep must cover the screen that leaked (not vacuous)')
        self.env['res.lang']._activate_lang('ar_001')
        self.env['ir.module.module']._load_module_terms(
            [m for m in ('ncollection_core', 'ncollection_subscription', 'ncollection_saas')
             if self.env['ir.module.module']._get(m).state == 'installed'], ['ar_001'], overwrite=True)
        for action in actions:
            for lang in ('en_US', 'ar_001'):
                with self.subTest(model=action.res_model, action=action.name, lang=lang):
                    title = self.env[action.res_model].sudo().with_context(lang=lang).new({}).display_name
                    self.assertFalse(_TECHNICAL.search(title or ''), 'technical title %r' % title)

    def test_an_odoo_screen_without_a_name_is_titled_by_its_model(self):
        """The generic half (ncollection_core/models/screen_title.py): a NEW record of a nameless model is titled by
        its model's description; a SAVED one keeps Odoo's display name."""
        if 'product.value' not in self.env:
            self.skipTest('stock is not installed here')  # noqa: listed in expected_skips only if it ever happens
        fresh = self.env['product.value'].sudo().new({})
        self.assertNotIn('NewId', fresh.display_name)
        self.assertEqual(fresh.display_name, self.env['ir.model']._get('product.value').name)

    def test_the_saas_summary_is_titled_like_its_menu_in_both_languages(self):
        self.env['res.lang']._activate_lang('ar_001')
        self.env['ir.module.module']._load_module_terms(['ncollection_subscription'], ['ar_001'], overwrite=True)
        dashboard = self.env['ncollection.subscription.dashboard'].sudo()
        self.assertEqual(dashboard.with_context(lang='en_US').new({}).display_name, 'Summary')
        self.assertEqual(dashboard.with_context(lang='ar_001').new({}).display_name, 'الملخّص')
        menu = self.env.ref('ncollection_subscription.menu_ncollection_saas_dashboard')
        self.assertEqual(menu.with_context(lang='ar_001').name, 'الملخّص', 'the breadcrumb matches the menu')
