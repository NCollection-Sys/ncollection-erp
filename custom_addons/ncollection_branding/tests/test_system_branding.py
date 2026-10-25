# -*- coding: utf-8 -*-
"""SYSTEM BRANDING: the system's identity, kept apart from the tenant's look and from the company's identity.

Three identities never share a field:
  * SYSTEM branding  (``ncollection.product.branding``, persisted name unchanged) - name, logos, favicon, browser
    title, copyright / ownership text, primary and secondary colours;
  * TENANT branding  (``res.company.nc_*_color`` and the login background) - how one workspace looks;
  * COMPANY identity (``res.company`` legal name, logo, registration) - who the company is.

Each test pins a promise that has a user-visible consequence. Synthetic data only.
"""
import os
import re

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged

HEX = re.compile(r'#[0-9A-Fa-f]{6}')
SYSTEM_PRIMARY, SYSTEM_SECONDARY = '#0B6E4F', '#C2571A'
TENANT_PRIMARY, TENANT_SECONDARY = '#7A1FA2', '#1B998B'
ADDONS = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..'))


def _style_block(page, style_id):
    """The text of ``<style id=style_id>`` in a rendered page, whitespace collapsed ('' when absent)."""
    match = re.search(r'<style[^>]*id="%s"[^>]*>(.*?)</style>' % re.escape(style_id), page, re.S)
    return re.sub(r'\s+', ' ', match.group(1)).strip() if match else ''


@tagged('post_install', '-at_install')
class TestSystemBrandingModel(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Brand = cls.env['ncollection.product.branding']
        cls.record = cls.Brand._nc_record()
        cls.plain = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Plain System User', 'login': 'plain_system',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])]})
        cls.portal = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Portal System User', 'login': 'portal_system',
            'group_ids': [(6, 0, [cls.env.ref('base.group_portal').id])]})

    # ----------------------------------------------------------------- the record and what it exposes
    def test_the_record_exposes_every_value_the_surfaces_need(self):
        self.record.write({
            'name': 'Acme Cloud', 'short_name': 'Acme', 'browser_title': 'Acme Console',
            'copyright_text': '© 2026 Acme Cloud Ltd. All rights reserved.',
            'primary_color': SYSTEM_PRIMARY, 'secondary_color': SYSTEM_SECONDARY})
        brand = self.Brand.nc_brand()
        self.assertEqual((brand['name'], brand['short_name'], brand['title']), ('Acme Cloud', 'Acme', 'Acme Console'))
        self.assertEqual(brand['copyright'], '© 2026 Acme Cloud Ltd. All rights reserved.')
        self.assertEqual((brand['primary_color'], brand['secondary_color']), (SYSTEM_PRIMARY, SYSTEM_SECONDARY))

    def test_the_browser_title_and_copyright_fall_back_to_the_name(self):
        self.record.write({'name': 'Acme Cloud', 'browser_title': False, 'copyright_text': False})
        brand = self.Brand.nc_brand()
        self.assertEqual(brand['title'], 'Acme Cloud')
        self.assertRegex(brand['copyright'], r'^© \d{4} Acme Cloud\. All rights reserved\.$')

    def test_a_workspace_that_never_set_the_colours_keeps_the_ncollection_palette(self):
        self.record.write({'primary_color': False, 'secondary_color': False})
        brand = self.Brand.nc_brand()
        self.assertEqual((brand['primary_color'], brand['secondary_color']), ('#1F5F8F', '#2D7AB7'))

    # ----------------------------------------------------------------- colours are safe CSS
    def test_only_a_strict_six_digit_hex_colour_is_stored_on_every_write_path(self):
        for good in ('#1F5F8F', '#abcdef', '#ABCDEF', False):
            self.record.write({'primary_color': good, 'secondary_color': good})
        hostile = ('red', '#12', '#12345', '#1234567', '1F5F8F', 'rgb(0,0,0)', 'url(javascript:alert(1))',
                   '#1F5F8F;}</style><script>alert(1)</script>', '#1F5F8F; background:url(x)', '#GGGGGG', ' #1F5F8F',
                   '#1F5F8F\n')
        for field in ('primary_color', 'secondary_color'):
            for bad in hostile:
                with self.subTest(field=field, value=bad), self.assertRaises(ValidationError):
                    self.record.write({field: bad})
        with self.assertRaises(ValidationError):
            self.record.write({'primary_color': '#1F5F8F;}'})
        self.assertEqual(self.record.primary_color, False)

    def test_a_corrupt_stored_colour_can_never_reach_a_page(self):
        """Even if a value got into the table some other way (raw SQL, an old import), nc_brand() emits the palette."""
        self.env.cr.execute("UPDATE ncollection_product_branding SET primary_color = %s, secondary_color = %s",
                            ('red;}</style>', 'url(x)'))
        self.env.invalidate_all()
        self.env.registry.clear_cache()
        brand = self.Brand.nc_brand()
        self.assertEqual((brand['primary_color'], brand['secondary_color']), ('#1F5F8F', '#2D7AB7'))

    def test_the_texts_are_bounded_and_clean(self):
        for field in ('copyright_text', 'browser_title'):
            with self.assertRaises(ValidationError):
                self.record.write({field: 'x' * 201})
            with self.assertRaises(ValidationError):
                self.record.write({field: 'line one\nline two'})
        self.record.write({'copyright_text': 'x' * 200})

    # ----------------------------------------------------------------- security (UI and RPC)
    def test_only_platform_staff_write_the_system_branding_over_rpc_as_well(self):
        for user in (self.plain, self.portal):
            with self.subTest(user=user.login):
                for call in (
                    lambda: self.record.with_user(user).write({'name': 'Hijacked'}),
                    lambda: self.record.with_user(user).write({'primary_color': '#000000'}),
                    lambda: self.record.with_user(user).write({'copyright_text': 'pwned'}),
                    lambda: self.Brand.with_user(user).create({'name': 'Hijacked'}),
                    lambda: self.record.with_user(user).unlink(),
                    lambda: self.Brand.with_user(user).action_open(),
                ):
                    with self.assertRaises(AccessError):
                        call()
        self.assertNotEqual(self.record.name, 'Hijacked')
        manager = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Branding Manager', 'login': 'branding_manager',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('ncollection_branding.group_system_branding_manager').id])]})
        self.record.with_user(manager).write({'primary_color': '#102030'})   # the holder of the right can
        self.assertEqual(self.Brand.nc_brand()['primary_color'], '#102030')

    def test_everybody_may_read_the_resolved_identity_because_every_page_does(self):
        for user in (self.plain, self.portal):
            brand = self.Brand.with_user(user).nc_brand()
            self.assertTrue(brand['name'] and brand['primary_color'] and brand['copyright'])

    def test_the_menu_and_the_access_row_stay_inside_the_platform_staff_boundary(self):
        manager = self.env.ref('ncollection_branding.group_system_branding_manager')
        menu = self.env.ref('ncollection_branding.menu_product_branding')
        self.assertEqual(menu.group_ids, manager)
        rules = self.env['ir.model.access'].sudo().search([('model_id.model', '=', 'ncollection.product.branding')])
        for rule in rules:
            self.assertEqual(rule.group_id, manager, 'only the System Branding Manager has any access row')
        self.assertFalse(rules.filtered(lambda r: r.perm_unlink), 'nobody can delete the system branding')

    # ----------------------------------------------------------------- naming, and the persisted row is kept
    def test_the_user_facing_concept_is_system_branding_and_the_persisted_names_are_unchanged(self):
        self.assertEqual(self.Brand._description, 'System branding')
        self.assertEqual(self.env.ref('ncollection_branding.menu_product_branding').name, 'System Branding')
        self.assertEqual(self.env.ref('ncollection_branding.action_product_branding').name, 'System Branding')
        self.assertEqual(self.env.ref('ncollection_core.menu_workspace_product_branding').name, 'System Branding')
        self.assertEqual(self.Brand.action_open()['name'], 'System Branding')
        arch = self.env.ref('ncollection_branding.view_product_branding_form').arch_db
        self.assertIn('System Branding', arch)
        self.assertNotIn('Product Branding', arch)
        self.env.cr.execute("SELECT to_regclass('ncollection_product_branding') IS NOT NULL, "
                            "to_regclass('ncollection_system_branding') IS NOT NULL")
        persisted, duplicate = self.env.cr.fetchone()
        self.assertTrue(persisted, 'the table the workspaces already hold their branding in is still the table')
        self.assertFalse(duplicate, 'there is exactly ONE system-branding table, never a second one')

    def test_labels_name_the_system_not_the_product(self):
        fields = self.Brand.fields_get(['name', 'logo', 'admin_logo', 'primary_color', 'secondary_color',
                                        'copyright_text', 'browser_title'])
        self.assertEqual(fields['name']['string'], 'System name')
        self.assertEqual(fields['logo']['string'], 'System logo')
        self.assertEqual(fields['admin_logo']['string'], 'Administration / system logo')
        self.assertEqual(fields['primary_color']['string'], 'System primary colour')
        self.assertEqual(fields['secondary_color']['string'], 'System secondary colour')
        for meta in self.Brand.fields_get().values():
            self.assertNotIn('Product', meta['string'], 'no label may still say Product')

    # ----------------------------------------------------------------- existing branding data
    def test_adopting_the_system_defaults_keeps_everything_that_was_configured(self):
        """A row from the old Product Branding screen has a name, a short name and images but no colours."""
        self.record.write({'name': 'Balance Cloud', 'short_name': 'Balance', 'browser_title': 'Balance Console'})
        self.env.cr.execute("UPDATE ncollection_product_branding SET primary_color = NULL, secondary_color = NULL")
        self.env.invalidate_all()
        self.assertEqual(self.Brand._nc_adopt_system_defaults(), 1)
        record = self.Brand._nc_record()
        self.assertEqual((record.name, record.short_name), ('Balance Cloud', 'Balance'),
                         'the configured name survives')
        self.assertEqual((record.primary_color, record.secondary_color), ('#1F5F8F', '#2D7AB7'))
        self.assertEqual(self.Brand._nc_adopt_system_defaults(), 0, 'running it again changes nothing')
        record.write({'primary_color': '#112233'})
        self.assertEqual(self.Brand._nc_adopt_system_defaults(), 0)
        self.assertEqual(record.primary_color, '#112233', 'a colour somebody chose is never changed')

    def test_the_migration_script_is_idempotent_and_only_fills_defaults(self):
        import importlib.util
        path = os.path.join(ADDONS, 'ncollection_branding', 'migrations', '19.0.1.11.0', 'post-migrate.py')
        spec = importlib.util.spec_from_file_location('nc_brand_migration', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.record.write({'name': 'Balance Cloud'})
        self.env.cr.execute("UPDATE ncollection_product_branding SET primary_color = NULL")
        self.env.invalidate_all()
        module.migrate(self.env.cr, '19.0.1.10.0')
        module.migrate(self.env.cr, '19.0.1.10.0')
        self.env.invalidate_all()
        record = self.Brand._nc_record()
        self.assertEqual((record.name, record.primary_color), ('Balance Cloud', '#1F5F8F'))
        module.migrate(self.env.cr, False)  # a fresh install has no previous version: nothing to do


@tagged('post_install', '-at_install')
class TestSystemTenantCompanyIsolation(HttpCase):
    """Both directions, and the company identity apart from both."""

    def setUp(self):
        super().setUp()
        self.Brand = self.env['ncollection.product.branding']
        self.record = self.Brand._nc_record()
        self.company = self.env.company
        self.record.write({'name': 'Acme Cloud', 'short_name': 'Acme', 'browser_title': 'Acme Console',
                           'copyright_text': '© 2026 Acme Cloud Ltd.', 'primary_color': SYSTEM_PRIMARY,
                           'secondary_color': SYSTEM_SECONDARY})
        self.company.write({'nc_primary_color': TENANT_PRIMARY, 'nc_secondary_color': TENANT_SECONDARY})
        self.env.cr.flush()

    def _system_identity(self):
        brand = dict(self.Brand.nc_brand())
        return {key: brand[key] for key in ('name', 'short_name', 'title', 'copyright', 'primary_color',
                                            'secondary_color', 'logo_url', 'login_logo_url', 'admin_logo_url',
                                            'favicon_url', 'version')}

    def _tenant_identity(self):
        company = self.company.invalidate_recordset() or self.company
        return (company.nc_primary_color, company.nc_secondary_color, company.nc_sidebar_color)

    # ----------------------------------------------------------------- tenant -> system
    def test_a_tenants_branding_cannot_change_the_system_identity(self):
        before = self._system_identity()
        self.company.write({'nc_primary_color': '#FF0000', 'nc_secondary_color': '#00FF00',
                            'nc_sidebar_color': '#0000FF', 'name': 'Some Tenant LLC'})
        self.company.partner_id.write({'name': 'Some Tenant LLC', 'vat': False})
        self.env.cr.flush()
        self.env.registry.clear_cache()
        self.assertEqual(self._system_identity(), before, 'nothing of the system moved')

    def test_the_sign_in_page_keeps_the_system_colours_whatever_the_tenant_chose(self):
        page = self.url_open('/web/login').text
        system, tenant = _style_block(page, 'nc_system_theme'), _style_block(page, 'nc_tenant_theme')
        self.assertIn('o_nc_system_surface', page, 'the sign-in page is marked as a system surface')
        self.assertIn(SYSTEM_PRIMARY, system)
        self.assertIn(TENANT_PRIMARY, tenant)
        # the rule that wins on the page body is the system's, and it holds no tenant value
        surface_rule = system.split('body.o_nc_system_surface', 1)[1]
        self.assertIn('--nc-color-primary: %s' % SYSTEM_PRIMARY, surface_rule)
        self.assertIn('--nc-color-secondary: %s' % SYSTEM_SECONDARY, surface_rule)
        self.assertNotIn(TENANT_PRIMARY, system)
        self.assertNotIn(TENANT_SECONDARY, system)
        # and the tenant block holds no system value: the two never mix
        self.assertNotIn(SYSTEM_PRIMARY, tenant)
        self.assertNotIn(SYSTEM_SECONDARY, tenant)

    def test_one_token_vocabulary_not_two(self):
        page = self.url_open('/web/login').text
        names = set(re.findall(r'--nc-[a-z-]+', _style_block(page, 'nc_system_theme')))
        self.assertTrue(names <= {'--nc-color-primary', '--nc-color-secondary', '--nc-sidebar-active'}, names)
        self.assertNotIn('--nc-system', page)
        self.assertNotIn('--nc-sys-', page)

    def test_the_tenants_colours_are_still_what_a_workspace_uses_inside_the_web_client(self):
        self.authenticate('admin', 'admin')
        page = self.url_open('/odoo').text
        system, tenant = _style_block(page, 'nc_system_theme'), _style_block(page, 'nc_tenant_theme')
        self.assertIn(TENANT_PRIMARY, tenant)
        self.assertLess(page.index('id="nc_system_theme"'), page.index('id="nc_tenant_theme"'),
                        'system first, tenant second: the tenant overrides inside its own workspace')
        self.assertNotIn('o_nc_system_surface', page.split('<body', 1)[1].split('>', 1)[0],
                         'the workspace is not a system surface')
        self.assertIn(SYSTEM_PRIMARY, system)

    def test_a_workspace_without_its_own_colour_inherits_the_systems(self):
        self.company.write({'nc_primary_color': False, 'nc_secondary_color': False})
        self.env.cr.flush()
        self.authenticate('admin', 'admin')
        page = self.url_open('/odoo').text
        self.assertEqual(_style_block(page, 'nc_tenant_theme').count('--nc-color-primary'), 0,
                         'the tenant emits nothing, so the system value in :root is what applies')
        self.assertIn('--nc-color-primary: %s' % SYSTEM_PRIMARY, _style_block(page, 'nc_system_theme'))

    # ----------------------------------------------------------------- system -> tenant
    def test_the_system_branding_cannot_change_a_tenants_branding_or_a_companys_identity(self):
        tenant_before = self._tenant_identity()
        company = self.company
        identity_before = (company.name, company.partner_id.name, company.partner_id.vat, company.currency_id,
                           company.logo, company.partner_id.company_registry)
        self.record.write({'name': 'Another System', 'primary_color': '#010203', 'secondary_color': '#040506',
                           'copyright_text': 'changed', 'logo': False, 'favicon': False,
                           'browser_title': 'Other tab'})
        self.env.cr.flush()
        self.env.registry.clear_cache()
        self.assertEqual(self._tenant_identity(), tenant_before, "a tenant's colours did not move")
        company = self.company.invalidate_recordset() or self.company
        self.assertEqual((company.name, company.partner_id.name, company.partner_id.vat, company.currency_id,
                          company.logo, company.partner_id.company_registry), identity_before,
                         "the company's identity did not move")

    def test_the_system_colours_changing_leaves_the_tenant_block_of_a_page_byte_identical(self):
        self.authenticate('admin', 'admin')
        before = _style_block(self.url_open('/odoo').text, 'nc_tenant_theme')
        self.record.write({'primary_color': '#0A0B0C', 'secondary_color': '#0D0E0F'})
        self.env.cr.flush()
        page = self.url_open('/odoo').text
        self.assertEqual(_style_block(page, 'nc_tenant_theme'), before)
        self.assertIn('#0A0B0C', _style_block(page, 'nc_system_theme'))

    # ----------------------------------------------------------------- company identity is a third, separate thing
    def test_a_companys_logo_name_and_registration_never_appear_in_the_system_identity(self):
        self.company.write({'name': 'Very Specific Tenant Name FZ-LLC'})
        self.env.cr.flush()
        self.env.registry.clear_cache()
        brand = self.Brand.nc_brand()
        self.assertNotIn('Very Specific Tenant Name', repr(brand))
        page = self.url_open('/web/login').text
        self.assertNotIn('Very Specific Tenant Name', _style_block(page, 'nc_system_theme'))
        self.assertIn('Acme Cloud', page, 'the sign-in page names the system, not the company')

    def test_system_branding_and_tenant_branding_are_written_by_different_models(self):
        self.assertNotEqual(self.Brand._name, self.company._name)
        for field in ('copyright_text', 'browser_title'):
            self.assertNotIn(field, self.company._fields)
        for field in ('primary_color', 'secondary_color'):
            # Odoo's own document-layout colours share these names on res.company: they are Odoo's, not ours, and
            # System Branding neither reads nor writes them
            theirs = self.company._fields.get(field)
            self.assertTrue(theirs is None or theirs._module != 'ncollection_branding', field)
        for field in ('nc_primary_color', 'nc_secondary_color', 'nc_sidebar_color', 'nc_login_background'):
            self.assertNotIn(field, self.Brand._fields)


@tagged('post_install', '-at_install')
class TestSystemBrandingSurfaces(HttpCase):
    """Every surface that names or paints the system resolves the configured values, and only those."""

    NAME, TITLE, COPY = 'Acme Cloud', 'Acme Console', '© 2026 Acme Cloud Ltd. Proprietary.'

    def setUp(self):
        super().setUp()
        self.Brand = self.env['ncollection.product.branding']
        self.record = self.Brand._nc_record()
        self.record.write({'name': self.NAME, 'short_name': 'Acme', 'browser_title': self.TITLE,
                           'copyright_text': self.COPY, 'primary_color': SYSTEM_PRIMARY,
                           'secondary_color': SYSTEM_SECONDARY})
        self.env.cr.flush()

    def _assert_no_other_identity(self, page, where):
        for forbidden in ('Balance Cloud', 'NCollection ERP'):
            self.assertNotIn(forbidden, page,
                             '%s still shows %r instead of the configured system identity' % (where, forbidden))

    def test_the_sign_in_page(self):
        page = self.url_open('/web/login').text
        self.assertRegex(page, r'<title>\s*%s\s*</title>' % re.escape(self.TITLE))
        self.assertIn(self.NAME, page)
        self.assertIn(self.COPY, page, 'the configured copyright / ownership line')
        self.assertNotIn('All rights reserved', page, 'the default line is not shown when one is configured')
        self._assert_no_other_identity(page, 'the sign-in page')

    def test_the_default_copyright_names_the_configured_system(self):
        self.record.write({'copyright_text': False})
        self.env.cr.flush()
        page = self.url_open('/web/login').text
        self.assertRegex(page, r'© \d{4} %s\. All rights reserved\.' % re.escape(self.NAME))

    def test_the_web_client_shell_title_theme_colour_and_session(self):
        self.authenticate('admin', 'admin')
        page = self.url_open('/odoo').text
        self.assertRegex(page, r'<title>\s*%s\s*</title>' % re.escape(self.TITLE))
        self.assertRegex(page, r'<meta name="theme-color" content="%s"' % re.escape(SYSTEM_PRIMARY))
        self.assertNotIn('#17375E', page, 'the old hard-coded theme colour is gone')
        info = self.url_open('/web/session/get_session_info', data='{"jsonrpc":"2.0","method":"call","params":{}}',
                             headers={'Content-Type': 'application/json'}).json()['result']
        brand = info['nc_product_brand']
        self.assertEqual((brand['name'], brand['title'], brand['copyright']), (self.NAME, self.TITLE, self.COPY))
        self.assertEqual((brand['primary_color'], brand['secondary_color']), (SYSTEM_PRIMARY, SYSTEM_SECONDARY))

    def test_the_error_pages_use_the_system_name_icon_and_colour(self):
        response = self.url_open('/this/page/does/not/exist/%d' % os.getpid())
        self.assertEqual(response.status_code, 404)
        page = response.text
        self.assertIn('--nc-brand: %s;' % SYSTEM_PRIMARY, page)
        # the tenant's own block (its default is that blue) is a different identity; the rest of the page holds none
        outside_tenant = re.sub(r'<style[^>]*id="nc_tenant_theme"[^>]*>.*?</style>', '', page, flags=re.S)
        self.assertNotIn('#1F5F8F', outside_tenant, 'no hard-coded system blue on the error page')
        self.assertIn('/ncollection/brand/favicon?v=', page)
        self.assertRegex(page, r'<title>[^<]*— %s</title>' % re.escape(self.TITLE),
                         'the error title ends with the configured browser title, not a typed name')
        self._assert_no_other_identity(page, 'the error page')

    def test_the_e_mail_frame_uses_the_system_name_and_colour(self):
        header = self.env['ir.qweb']._render('ncollection_branding.nc_email_logo_header', {})
        self.assertIn('3px solid %s' % SYSTEM_PRIMARY, header)
        self.assertIn('alt="%s"' % self.NAME, header)
        self.assertNotIn('#1F5F8F', header)

    def test_a_system_that_is_not_ncollection_never_shows_the_default_name(self):
        for path in ('/web/login', '/web/reset_password'):
            self._assert_no_other_identity(self.url_open(path).text, path)

    def test_the_sign_in_page_in_arabic_is_right_to_left_and_still_system_branded(self):
        self.env['res.lang']._activate_lang('ar_001')
        page = self.url_open('/web/login', headers={'Accept-Language': 'ar'}).text
        self.assertIn(self.NAME, page)
        self.assertIn(self.COPY, page, 'the configured line is shown verbatim: it is data, not a translated term')
        # Odoo's sign-in layout carries no lang attribute: the proof of Arabic is its translated button or RTL assets
        self.assertRegex(page, r'تسجيل الدخول|\.rtl\.', 'the page really rendered in Arabic')
        self.assertIn('--nc-color-primary: %s' % SYSTEM_PRIMARY, _style_block(page, 'nc_system_theme'))
        # direction is Odoo's and the stylesheet's logical properties: the colour tokens carry no left/right at all
        self.assertNotRegex(_style_block(page, 'nc_system_theme'), r'(left|right|margin|padding)')


@tagged('post_install', '-at_install')
class TestNoHardcodedSystemIdentity(TransactionCase):
    """The system name, the old product name and the old colours are resolved from System Branding, never typed."""

    RUNTIME_SUFFIXES = ('.py', '.xml', '.js', '.scss', '.csv')
    SKIP_DIRS = {'tests', 'i18n', '__pycache__', 'node_modules', 'migrations', 'static_description', 'description'}

    def _runtime_files(self):
        for module in sorted(os.listdir(ADDONS)):
            if not module.startswith('ncollection_'):
                continue
            for root, dirs, files in os.walk(os.path.join(ADDONS, module)):
                dirs[:] = [d for d in dirs if d not in self.SKIP_DIRS]
                for name in files:
                    if name.endswith(self.RUNTIME_SUFFIXES):
                        yield os.path.join(root, name)

    def test_no_runtime_file_names_balance_cloud(self):
        offenders = []
        for path in self._runtime_files():
            with open(path, encoding='utf-8', errors='replace') as handle:
                if re.search(r'balance[ _-]?cloud', handle.read(), re.I):
                    offenders.append(os.path.relpath(path, ADDONS))
        self.assertEqual(offenders, [], "a configured value (the system name) must never be a literal in runtime code")

    def test_the_system_name_literal_lives_only_where_the_shipped_default_is_defined(self):
        allowed = {
            os.path.join('ncollection_branding', 'models', 'product_branding.py'),   # DEFAULT_NAME
            os.path.join('ncollection_branding', 'data', 'product_branding_data.xml'),  # the default row, noupdate
            os.path.join('ncollection_branding', 'static', 'src', 'js', 'white_label.js'),  # last resort, no session
            os.path.join('ncollection_branding', 'data', 'system_parameters.xml'),   # seeded, unread parameters
            os.path.join('ncollection_branding', 'views', 'product_branding_views.xml'),  # a placeholder example
            os.path.join('ncollection_core', 'security', 'role_groups.xml'),   # role catalogue description
            os.path.join('ncollection_subscription', 'data', 'demo_data.xml'),   # demo plan description, no surface
        }
        offenders = []
        for path in self._runtime_files():
            relative = os.path.relpath(path, ADDONS)
            if relative in allowed or relative.endswith('__manifest__.py') or relative.endswith('.csv'):
                continue
            with open(path, encoding='utf-8', errors='replace') as handle:
                for number, line in enumerate(handle, 1):
                    stripped = line.strip()
                    if 'NCollection ERP' in line and not stripped.startswith(('#', '<!--', '*', '//', '"""', "'''")):
                        offenders.append('%s:%d' % (relative, number))
        self.assertEqual(offenders, [], 'resolve the name through nc_brand() / nc_product_brand()')

    def test_no_system_colour_literal_in_the_system_surfaces(self):
        for relative in ('views/http_error_templates.xml', 'views/mail_layout_templates.xml',
                         'views/webclient_templates.xml'):
            with open(os.path.join(ADDONS, 'ncollection_branding', relative), encoding='utf-8') as handle:
                text = re.sub(r'<!--.*?-->', '', handle.read(), flags=re.S)
            for literal in ('#1F5F8F', '#17375E'):
                self.assertNotIn(literal, text, '%s still hard-codes %s' % (relative, literal))

    def test_the_old_wording_is_gone_from_every_menu_and_action(self):
        self.env.invalidate_all()
        menus = self.env['ir.ui.menu'].with_context(lang='en_US').search([('name', 'ilike', 'Product Branding')])
        self.assertFalse(menus, 'no menu still says Product Branding')
        actions = self.env['ir.actions.server'].with_context(lang='en_US').search(
            [('name', 'ilike', 'Product Branding')])
        self.assertFalse(actions)
