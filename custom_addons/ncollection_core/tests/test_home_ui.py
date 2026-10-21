# -*- coding: utf-8 -*-
"""Home launcher: real icons for every NCollection application, stable menu/action identity, the larger tile, and the
Arabic / English names.

There is ONE source for what a tile says and shows: the root ``ir.ui.menu`` record (``nc_tenant_apps`` reads its name
and ``web_icon_data``). So these tests look at the menu records, not at a list kept in the client.
"""
import os

from odoo.tests import TransactionCase, tagged

from ..models.ir_ui_menu import LAUNCHER_EXCLUDED_MENUS

_CORE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# the identity of the NCollection applications that were already on Home; none of these may move or change action
_APP_ROOTS = {
    'ncollection_core.menu_ncollection_dashboard_root': 'ncollection_core.action_ncollection_dashboard',
    'ncollection_core.menu_ncollection_alert': 'ncollection_core.action_ncollection_alert',
    'ncollection_account_reports.menu_nc_reports_root': 'ncollection_account_reports.action_nc_report_hub',
    'ncollection_stock_barcode.menu_nc_barcode_root': 'ncollection_stock_barcode.action_nc_barcode_scanner',
}


@tagged('post_install', '-at_install')
class TestHomeUi(TransactionCase):

    def _read(self, rel):
        with open(os.path.join(_CORE, *rel.split('/')), encoding='utf-8') as fh:
            return fh.read()

    def _roots(self):
        Menu = self.env['ir.ui.menu'].with_context(active_test=False)
        roots = Menu.search([('parent_id', '=', False)])
        xmlids = roots.sudo()._get_menuitems_xmlids()
        return [(menu, xmlids.get(menu.id, '')) for menu in roots]

    # ------------------------------------------------------------------ real icons
    def test_no_ncollection_home_application_falls_back_to_the_letter(self):
        """The launcher shows a letter only when a root menu has no icon. For every NCollection root that is an
        application (has something to open, is not the launcher itself or internal tooling) the icon must exist, so a
        new NCollection app added without one fails here instead of showing a lettermark on a customer's Home."""
        without = []
        checked = 0
        for menu, xmlid in self._roots():
            if not xmlid.startswith('ncollection_') or xmlid in LAUNCHER_EXCLUDED_MENUS:
                continue
            if not menu._nc_has_reachable_action():
                continue
            checked += 1
            if not menu.web_icon_data:
                without.append('%s (web_icon=%r)' % (xmlid, menu.web_icon))
        self.assertGreaterEqual(checked, 5, "the NCollection applications on Home were not found")
        self.assertFalse(without, "NCollection application(s) with no icon: %s" % without)

    def test_the_payload_carries_icon_data_for_an_app_with_an_icon(self):
        owner = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'home owner', 'login': 'home_ui_owner',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('ncollection_core.group_role_owner').id])]})
        apps = {a['xmlid']: a for a in self.env['ir.ui.menu'].with_user(owner).nc_tenant_apps()}
        summary = apps.get('ncollection_core.menu_ncollection_dashboard_root')
        self.assertTrue(summary, "the Summary tile must be on Home")
        self.assertTrue(summary['web_icon_data'], "its icon travels with the tile, so the client never shows a letter")

    def test_the_icon_files_are_the_family_size_and_load_from_the_modules_own_path(self):
        for module, name in (('ncollection_core', 'icon_summary.png'), ('ncollection_core', 'icon_alerts.png'),
                             ('ncollection_core', 'icon_workspace.png'), ('ncollection_account_dashboard', 'icon.png'),
                             ('ncollection_reseller', 'icon.png'), ('ncollection_stock_barcode', 'icon.png'),
                             ('ncollection_account_reports', 'icon.png'), ('ncollection_subscription', 'icon.png'),
                             ('ncollection_branding', 'icon.png')):
            path = os.path.join(os.path.dirname(_CORE), module, 'static', 'description', name)
            self.assertTrue(os.path.exists(path), path)
            with open(path, 'rb') as fh:
                head = fh.read(24)
            self.assertEqual(head[:8], b'\x89PNG\r\n\x1a\n', "%s is not a PNG" % name)
            size = (int.from_bytes(head[16:20], 'big'), int.from_bytes(head[20:24], 'big'))
            self.assertEqual(size, (128, 128), name)

    # ------------------------------------------------------------------ identity does not move
    def test_existing_menu_and_action_ids_are_unchanged(self):
        for menu_xmlid, action_xmlid in _APP_ROOTS.items():
            menu = self.env.ref(menu_xmlid)
            self.assertFalse(menu.parent_id, menu_xmlid)
            self.assertEqual(menu.action, self.env.ref(action_xmlid), menu_xmlid)
        accounting = self.env.ref('account.menu_finance')
        self.assertFalse(accounting.parent_id)

    # ------------------------------------------------------------------ the larger tile
    def test_the_tile_is_scaled_as_a_whole_from_one_knob(self):
        scss = self._read('static/src/home/tenant_home.scss')
        self.assertIn('--o-nc-home-scale: 1.2', scss)
        # the card, the icon, the spacing and the label all read the knob, so they stay in proportion
        for part in ('grid-template-columns', 'gap: calc(var(--nc-space-3)', 'padding: calc(var(--nc-space-4)',
                     'inline-size: calc(2.5rem', 'block-size: calc(2.5rem', 'font-size: calc(var(--nc-font-size-sm)'):
            self.assertIn(part, scss)
        self.assertGreaterEqual(scss.count('var(--o-nc-home-scale)'), 8)
        # no fixed-size icon left behind inside an enlarged card
        self.assertNotIn('inline-size: 2.5rem', scss)

    def test_the_tile_template_still_has_one_card_design_and_a_letter_fallback_only_for_icon_less_menus(self):
        xml = self._read('static/src/home/tenant_home.xml')
        self.assertEqual(xml.count('class="o_nc_home__card nc-card nc-card--interactive"'), 1)
        self.assertIn('t-if="app.web_icon_data"', xml)
        self.assertIn('o_nc_home__lettermark', xml)

    # ------------------------------------------------------------------ English and Arabic
    def _arabic(self):
        self.env['res.lang']._activate_lang('ar_001')
        self.env['ir.module.module']._load_module_terms(
            ['ncollection_core', 'ncollection_stock_barcode', 'ncollection_account_dashboard',
             'ncollection_account_core', 'ncollection_account_reports'], ['ar_001'], overwrite=True)

    def test_home_application_names_are_arabic_in_an_arabic_session_and_english_otherwise(self):
        self._arabic()
        expected = {
            'ncollection_core.menu_ncollection_dashboard_root': ('Summary', 'الملخّص'),
            'ncollection_core.menu_ncollection_alert': ('Anomaly Alerts', 'تنبيهات الشذوذ'),
            'ncollection_core.menu_workspace_settings_root': ('Workspace Administration', 'إدارة مساحة العمل'),
            'ncollection_stock_barcode.menu_nc_barcode_root': ('Barcode', 'الباركود'),
        }
        for xmlid, (english, arabic) in expected.items():
            menu = self.env.ref(xmlid)
            self.assertEqual(menu.with_context(lang='en_US').name, english, xmlid)
            self.assertEqual(menu.with_context(lang='ar_001').name, arabic, xmlid)

    def test_the_renamed_accounting_app_is_not_called_invoicing_in_arabic(self):
        """Renaming Odoo's menu changes the English source only; the Arabic Odoo already had said Invoicing."""
        self._arabic()
        menu = self.env.ref('account.menu_finance')
        self.assertEqual(menu.with_context(lang='en_US').name, 'Accounting')
        self.assertEqual(menu.with_context(lang='ar_001').name, 'المحاسبة')

    def test_the_launcher_own_strings_are_translated_for_the_web_client(self):
        """The OWL launcher's text reaches the browser through the web-client translation bundle, not the database."""
        modules, _lang = self.env['ir.http']._get_translations_for_webclient(['ncollection_core'], 'ar_001')
        messages = {m['id']: m['string'] for m in modules.get('ncollection_core', {}).get('messages', [])}
        self.assertEqual(messages.get('Your applications'), 'تطبيقاتك')
        self.assertEqual(messages.get('No applications yet'), 'لا توجد تطبيقات بعد')
        self.assertEqual(messages.get('Loading your applications…'), 'جارٍ تحميل تطبيقاتك…')
