# -*- coding: utf-8 -*-
"""Custom tenant domains, tenant side: the database pin, the canonical host service and the redirect rule."""
import os
from types import SimpleNamespace
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.ncollection_core import tenant_host_pin
from odoo.addons.ncollection_core.models.canonical_host import IrHttpCanonicalHost

ACTOR = {'ref': 'u7', 'name': 'Platform Admin'}


def _original(dbs, host=None):
    return [db for db in dbs if db == (host or '').partition('.')[0]]


class _Request(SimpleNamespace):
    pass


@tagged('post_install', '-at_install')
class TestTenantHostPin(TransactionCase):

    def _filter(self, headers, env_value, dbs=('acme', 'globex', 'atlas-core'), host='globex.platform.example'):
        request = _Request(httprequest=SimpleNamespace(headers=headers))
        with patch.dict(os.environ, {tenant_host_pin.ENV: env_value} if env_value is not None else {}, clear=False), \
                patch.object(tenant_host_pin.http, 'request', request):
            if env_value is None:
                os.environ.pop(tenant_host_pin.ENV, None)
            return tenant_host_pin.pinned_db_filter(list(dbs), host, _original=_original)

    def test_without_the_switch_the_header_is_ignored(self):
        self.assertEqual(self._filter({'X-NC-Tenant-DB': 'acme'}, None), ['globex'])
        self.assertEqual(self._filter({'X-NC-Tenant-DB': 'acme'}, '0'), ['globex'])

    def test_with_the_switch_the_pin_is_the_only_reachable_database(self):
        self.assertEqual(self._filter({'X-NC-Tenant-DB': 'acme'}, '1'), ['acme'])
        self.assertEqual(self._filter({'X-NC-Tenant-DB': 'nosuchdb'}, '1'), [])

    def test_no_header_keeps_subdomain_routing(self):
        self.assertEqual(self._filter({}, '1'), ['globex'])

    def test_a_malformed_pin_reaches_no_database(self):
        for value in ('', 'Acme', 'acme;drop', '../acme', 'a' * 80, 'acme globex'):
            self.assertEqual(self._filter({'X-NC-Tenant-DB': value}, '1'), [], value)

    def test_the_seam_is_installed_by_the_server_wide_module(self):
        tenant_host_pin.install()
        self.assertTrue(tenant_host_pin.installed())
        tenant_host_pin.install()   # idempotent: never wraps twice
        self.assertFalse(getattr(tenant_host_pin.http.db_filter.__wrapped__, '_nc_host_pin', False))


@tagged('post_install', '-at_install')
class TestCanonicalHost(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        cls.service_user = Users.create({
            'name': 'Config Sync Service', 'login': 'canon.sync@ncollection.internal',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('ncollection_core.group_config_sync').id])]})
        cls.member = Users.create({'name': 'Member', 'login': 'canon.member',
                                   'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])]})
        cls.service = cls.env['ncollection.admin.service'].with_user(cls.service_user)
        cls.params = cls.env['ir.config_parameter'].sudo()

    def _set(self, **vals):
        vals.setdefault('actor', dict(ACTOR))
        return self.service.set_canonical_host(vals)

    def test_the_platform_sets_the_canonical_host_and_links_follow_it(self):
        reply = self._set(host='erp.customer.com', redirect=True,
                          aliases=['acme.platform.example', 'erp.customer.com'])
        self.assertEqual(reply, {'ok': True, 'host': 'erp.customer.com', 'redirect': True})
        self.assertEqual(self.params.get_param('web.base.url'), 'https://erp.customer.com')
        self.assertEqual(self.params.get_param('web.base.url.freeze'), 'True')
        self.assertEqual(self.params.get_param('ncollection.host_aliases'), 'acme.platform.example,erp.customer.com')
        self._set(host='acme.platform.example', redirect=False, aliases=['acme.platform.example'])
        self.assertEqual(self.params.get_param('web.base.url'), 'https://acme.platform.example')

    def test_only_the_platform_service_channel_may_change_it(self):
        with self.assertRaises(AccessError):
            self.env['ncollection.admin.service'].with_user(self.member).set_canonical_host(
                {'actor': ACTOR, 'host': 'evil.example.com', 'redirect': True, 'aliases': ['evil.example.com']})

    def test_malformed_or_unlisted_hosts_are_refused(self):
        for vals in ({'host': 'evil.example.com', 'redirect': True, 'aliases': ['acme.platform.example']},
                     {'host': 'https://x.com', 'redirect': False, 'aliases': ['https://x.com']},
                     {'host': 'x.com', 'redirect': 'yes', 'aliases': ['x.com']},
                     {'host': 'x.com', 'redirect': False, 'aliases': 'x.com'},
                     {'host': 'x.com', 'redirect': False, 'aliases': []},
                     {'host': 'x.com', 'redirect': False, 'aliases': ['x.com'], 'extra': 1}):
            with self.assertRaises(UserError, msg=vals):
                self._set(**vals)
        self.assertNotEqual(self.params.get_param('web.base.url'), 'https://evil.example.com')

    def _target(self, host, path='/web/login', method='GET', query=b''):
        req = SimpleNamespace(method=method, host=host, path=path, query_string=query)
        return IrHttpCanonicalHost._nc_canonical_redirect_target(req, self.params)

    def test_redirect_only_from_a_known_alias_to_the_canonical_host(self):
        self._set(host='erp.customer.com', redirect=True, aliases=['acme.platform.example', 'erp.customer.com'])
        self.assertEqual(self._target('acme.platform.example', query=b'redirect=%2Fodoo'),
                         'https://erp.customer.com/web/login?redirect=%2Fodoo')
        self.assertIsNone(self._target('erp.customer.com'), 'never a loop')
        self.assertIsNone(self._target('attacker.example.net'), 'an unknown host is never redirected anywhere')
        self.assertIsNone(self._target('acme.platform.example', method='POST'))
        for path in ('/json/2/res.partner/search', '/jsonrpc', '/web/dataset/call_kw', '/websocket', '/web/health',
                     '/ncollection/release/status', '/web/assets/x.js', '/api/v1/oauth/token'):
            self.assertIsNone(self._target('acme.platform.example', path=path), path)

    def test_alias_policy_never_redirects(self):
        self._set(host='erp.customer.com', redirect=False, aliases=['acme.platform.example', 'erp.customer.com'])
        self.assertIsNone(self._target('acme.platform.example'))
