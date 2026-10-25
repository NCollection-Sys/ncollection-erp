# -*- coding: utf-8 -*-
"""The public application path (app_url.py, controllers/app_routes.py, static/src/router/app_path.js): /atlas/...
in the address bar, never /odoo/...; /web/... untouched.

The HTTP half runs against Odoo's real test server (routing, session, redirects). The browser half - the router
conversions, deep links, back / forward, the click routing - is proven in a real browser by
e2e/tests/partner-roles-and-titles.spec.ts ("Application URLs"); here its SEAM is pinned: the exact places in Odoo's
router.js the patch relies on, so an Odoo update that moves them fails CI instead of silently showing /odoo again.
"""
import os
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

from odoo.tests import HttpCase, TransactionCase, tagged
from odoo.tools import file_open

from odoo.addons.ncollection_core import app_url as urls


@tagged('post_install', '-at_install')
class TestApplicationUrlBuilder(TransactionCase):

    def test_the_one_builder(self):
        self.assertEqual(urls.APP_PATH, '/atlas', 'the default in development and production')
        self.assertEqual(urls.app_url(), '/atlas')
        self.assertEqual(urls.app_url('action-12/new'), '/atlas/action-12/new')
        self.assertEqual(urls.app_url('/res.partner/5'), '/atlas/res.partner/5')
        self.assertEqual(urls.app_url('?debug=1'), '/atlas?debug=1')

    def test_odoo_application_urls_are_mapped_and_nothing_else(self):
        cases = {
            '/odoo': '/atlas',
            '/odoo/action-169/new': '/atlas/action-169/new',
            '/odoo/res.partner/5?debug=1#x': '/atlas/res.partner/5?debug=1#x',
            'https://t.example.com/odoo/discuss': 'https://t.example.com/atlas/discuss',
            '/web/login': '/web/login', '/web/image/res.partner/1/avatar_128': '/web/image/res.partner/1/avatar_128',
            '/web/content/12?download=true': '/web/content/12?download=true', '/odooish': '/odooish',
            '/report/pdf/account.report_invoice/7': '/report/pdf/account.report_invoice/7', '': '',
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(urls.to_app_url(url), expected)

    def test_the_prefix_is_configuration_and_never_an_odoo_namespace(self):
        for value, expected in (('/atlas', '/atlas'), ('/console/', '/console'), ('/web', '/atlas'),
                                ('/odoo', '/atlas'), ('atlas', '/atlas'), ('/At las', '/atlas'), ('/api', '/atlas')):
            with self.subTest(value=value), patch.dict(os.environ, {urls.APP_PATH_ENV: value}), \
                    self.assertLogs(urls.__name__, 'CRITICAL') if expected != value.rstrip('/') else _quiet():
                self.assertEqual(urls._read_app_path(), expected)

    def test_the_odoo_router_seams_the_patch_relies_on_are_still_there(self):
        """app_path.js uses ONLY router.stateToUrl / router.urlToState (exported for exactly this) and the exported
        startRouter(); this pins that Odoo still routes every conversion through them."""
        with file_open('web/static/src/core/browser/router.js') as handle:
            source = handle.read()
        for needle in ('export function startRouter()', '    stateToUrl,\n', '    urlToState,\n',
                       'const url = browser.location.origin + router.stateToUrl(nextState);',
                       'state = ev.state?.nextState || router.urlToState(new URL(browser.location));',
                       'state = router.urlToState(url);', 'return `/${start_url}${path}',
                       'if (["odoo", "scoped_app"].includes(prefix)) {'):
            with self.subTest(needle=needle):
                self.assertIn(needle, source)
        assets = [path for path, *_rest in self.env['ir.asset']._get_asset_paths('web.assets_backend', {})]
        router_at = next(i for i, p in enumerate(assets) if p.endswith('web/static/src/core/browser/router.js'))
        patch_at = next(i for i, p in enumerate(assets)
                        if p.endswith('ncollection_core/static/src/router/app_path.js'))
        self.assertLess(router_at, patch_at, 'the patch loads after the router it patches')


class _quiet:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@tagged('post_install', '-at_install')
class TestApplicationUrlHttp(HttpCase):

    def _get(self, path):
        return self.url_open(path, allow_redirects=False)

    def _location(self, response):
        return urlsplit(response.headers.get('Location', ''))

    def test_odoo_application_urls_redirect_to_the_application_path_and_keep_the_query(self):
        response = self._get('/odoo/action-169/new?debug=1')
        self.assertEqual(response.status_code, 302)
        location = self._location(response)
        self.assertEqual((location.path, location.query), ('/atlas/action-169/new', 'debug=1'))
        self.assertEqual(self._location(self._get('/odoo')).path, '/atlas')

    def test_an_anonymous_visitor_logs_in_and_comes_back_to_the_application_path(self):
        response = self._get('/atlas/action-169/new')
        self.assertEqual(response.status_code, 303)
        location = self._location(response)
        self.assertEqual(location.path, '/web/login')
        self.assertIn('redirect=/atlas/action-169/new', unquote(location.query), 'session expiry returns here')

    def test_the_application_path_serves_the_web_client(self):
        self.authenticate('admin', 'admin')
        for path in ('/atlas', '/atlas/action-base.action_res_users', '/atlas/res.partner/1'):
            with self.subTest(path=path):
                response = self._get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn('"nc_app_path": "/atlas"', response.text)
        self.assertEqual(self._location(self._get('/')).path, '/atlas', 'the root goes to the application path')

    def test_logout_ends_on_the_login_page_and_logging_in_returns_to_the_application_path(self):
        self.authenticate('admin', 'admin')
        response = self._get('/web/session/logout')
        self.assertEqual(self._location(response).path, '/atlas')
        response = self.url_open('/atlas')            # follows: no session any more
        self.assertIn('/web/login', response.url)
        self.assertIn('redirect=/atlas', unquote(response.url))

    def test_odoo_technical_endpoints_keep_their_web_path(self):
        for path in ('/web/login', '/web/health', '/web/webclient/version_info'):
            with self.subTest(path=path):
                response = self.url_open(path, data='{}' if 'version_info' in path else None,
                                         headers={'Content-Type': 'application/json'} if 'version_info' in path
                                         else None, allow_redirects=False)
                self.assertIn(response.status_code, (200,), path)

    def test_the_installable_app_lives_under_the_application_path(self):
        from odoo.addons.ncollection_core.controllers.app_routes import SW_HOMEPAGE, SW_SHARE_TARGET
        manifest = self.url_open('/web/manifest.webmanifest').json()
        self.assertEqual((manifest['scope'], manifest['start_url']), ('/atlas', '/atlas'))
        self.assertFalse([s for s in manifest.get('shortcuts', []) if s['url'].startswith('/odoo')])
        worker = self.url_open('/web/service-worker.js')
        self.assertEqual(worker.headers['Service-Worker-Allowed'], '/atlas')
        self.assertIn('const homepageURL = "/atlas";', worker.text)
        self.assertNotIn('"/odoo', worker.text)
        with file_open('web/static/src/service_worker.js') as handle:
            source = handle.read()
        for pinned in (SW_HOMEPAGE, SW_SHARE_TARGET):
            with self.subTest(pinned=pinned):
                self.assertIn(pinned, source, 'Odoo changed its service worker: adapt app_routes.py')
        with file_open('web/static/src/webclient/webclient.js') as handle:
            self.assertIn('.register("/web/service-worker.js", { scope: "/odoo" })', handle.read())
