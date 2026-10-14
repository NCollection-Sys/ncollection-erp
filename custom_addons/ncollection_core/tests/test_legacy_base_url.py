# -*- coding: utf-8 -*-
"""A tenant database still carrying the retired dev-template web.base.url is moved to the platform base domain; any
other value is never touched."""
import os
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestLegacyBaseUrl(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Config = self.env['ncollection.workspace.config']
        self.Param = self.env['ir.config_parameter'].sudo()
        self.db = self.env.cr.dbname

    def _legacy(self):
        self.Param.set_param('web.base.url', 'http://%s.localhost:8069' % self.db)

    def test_the_legacy_value_moves_to_the_base_domain_over_http_for_loopback(self):
        self._legacy()
        with patch.dict(os.environ, {'NC_BASE_DOMAIN': 'ncollection.localhost'}):
            self.assertEqual(self.Config._nc_repair_legacy_base_url(), 'http://%s.ncollection.localhost' % self.db)
        self.assertEqual(self.Param.get_param('web.base.url'), 'http://%s.ncollection.localhost' % self.db)

    def test_a_real_domain_is_https(self):
        self._legacy()
        with patch.dict(os.environ, {'NC_BASE_DOMAIN': 'example.com'}):
            self.Config._nc_repair_legacy_base_url()
        self.assertEqual(self.Param.get_param('web.base.url'), 'https://%s.example.com' % self.db)

    def test_any_other_value_is_left_alone(self):
        self.Param.set_param('web.base.url', 'https://erp.customer.example')
        with patch.dict(os.environ, {'NC_BASE_DOMAIN': 'example.com'}):
            self.assertFalse(self.Config._nc_repair_legacy_base_url())
        self.assertEqual(self.Param.get_param('web.base.url'), 'https://erp.customer.example')

    def test_nothing_happens_when_the_deployment_names_no_domain(self):
        self._legacy()
        with patch.dict(os.environ, {'NC_BASE_DOMAIN': ''}):
            self.assertFalse(self.Config._nc_repair_legacy_base_url())
        self.assertEqual(self.Param.get_param('web.base.url'), 'http://%s.localhost:8069' % self.db)

    def test_it_is_idempotent(self):
        self._legacy()
        with patch.dict(os.environ, {'NC_BASE_DOMAIN': 'ncollection.localhost'}):
            self.assertTrue(self.Config._nc_repair_legacy_base_url())
            self.assertFalse(self.Config._nc_repair_legacy_base_url())
