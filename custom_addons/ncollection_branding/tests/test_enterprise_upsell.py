# -*- coding: utf-8 -*-
"""The Enterprise upsell is closed in Settings, for every installed app.

Odoo Community renders each Enterprise-only option with an upsell widget whose click opens the "Odoo Enterprise - Get
this feature and much more - Upgrade now" dialog. The settings view the web client receives (``get_views``, i.e.
after ``_get_view``) must not show a single one, in ANY app: the sweep below covers every installed module's settings
at once and names the apps it covered, so it cannot pass vacuously. The client half (no dialog even if a widget is
rendered somewhere) is proven in a real browser by e2e/tests/partner-roles-and-titles.spec.ts.
"""
from lxml import etree

from odoo.tests import TransactionCase, tagged
from odoo.tools import file_open

from odoo.addons.ncollection_branding.models.res_config_settings import UPSELL_WIDGETS


@tagged('post_install', '-at_install')
class TestEnterpriseUpsellClosed(TransactionCase):

    def _settings_arch(self):
        return etree.fromstring(self.env['res.config.settings'].get_views([(False, 'form')])['views']['form']['arch'])

    @staticmethod
    def _hidden(node):
        return any(n.get('invisible') in ('1', 'True') for n in [node, *node.iterancestors()])

    def test_no_upsell_option_is_visible_in_any_app(self):
        arch = self._settings_arch()
        upsells = arch.xpath(' | '.join("//field[@widget='%s']" % w for w in UPSELL_WIDGETS))
        apps = {app.get('name') for field in upsells for app in field.iterancestors() if app.tag == 'app'}
        self.assertGreaterEqual(len(upsells), 5, 'the sweep must meet real upsell options (not vacuous)')
        self.assertGreaterEqual(len(apps), 3, 'across several apps: %s' % sorted(apps))
        visible = [(f.get('name'), next((a.get('name') for a in f.iterancestors() if a.tag == 'app'), '?'))
                   for f in upsells if not self._hidden(f)]
        self.assertEqual(visible, [], 'Enterprise upsell options still reachable')

    def test_ordinary_settings_stay_available(self):
        """Closing the upsell does not empty Settings: real, Community options remain visible."""
        arch = self._settings_arch()
        visible_settings = [s for s in arch.xpath('//setting') if not self._hidden(s)]
        self.assertGreater(len(visible_settings), 10)
        visible_fields = {f.get('name') for s in visible_settings for f in s.xpath('.//field')}
        # an ordinary Community option (a module_* one is hidden by Odoo itself when its module is absent)
        for name in ('group_multi_currency',):
            if name in self.env['res.config.settings']._fields:
                with self.subTest(field=name):
                    self.assertIn(name, visible_fields)

    def test_the_widget_itself_never_opens_the_dialog(self):
        with file_open('ncollection_branding/static/src/js/no_enterprise_upsell.js') as handle:
            source = handle.read()
        self.assertIn('patch(UpgradeBooleanField.prototype', source)
        self.assertNotIn('UpgradeDialog', source.split('*/', 1)[1], 'the patch never opens the upsell dialog')
        assets = [path for path, *_rest in self.env['ir.asset']._get_asset_paths('web.assets_backend', {})]
        self.assertTrue(any(p.endswith('ncollection_branding/static/src/js/no_enterprise_upsell.js') for p in assets))
