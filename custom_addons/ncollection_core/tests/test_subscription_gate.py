# -*- coding: utf-8 -*-
"""Suspended/expired workspace interstitial (P2-T03).

The gate (models/subscription_gate.py) serves a branded "Subscription paused"
page in place of the backend web client when the tenant's projected status is
suspended/expired — while never touching the login page or assets, and failing
open on any error.
"""
from odoo.tests import HttpCase, TransactionCase, tagged


def _set_status(env, status):
    cfg = env['ncollection.workspace.config'].get_config()
    if cfg:
        cfg.write({'subscription_status': status})
    else:
        env['ncollection.workspace.config'].create({'subscription_status': status})
    env.flush_all()


@tagged('post_install', '-at_install')
class TestSubscriptionGate(TransactionCase):

    def test_template_and_group_exist(self):
        # the interstitial template + the least-privilege config-sync group.
        # (full rendering needs a request/frontend_layout — see the HttpCase.)
        self.assertTrue(self.env.ref('ncollection_core.subscription_blocked'))
        self.assertTrue(self.env.ref('ncollection_core.group_config_sync'))


@tagged('post_install', '-at_install')
class TestSubscriptionGateHttp(HttpCase):

    def test_suspended_blocks_backend_but_not_login(self):
        # HttpCase shares the test transaction, so the write is visible to the
        # request once flushed — no commit needed.
        _set_status(self.env, 'suspended')
        self.authenticate('admin', 'admin')
        # backend web client -> interstitial
        res = self.url_open('/odoo')
        self.assertEqual(res.status_code, 200)
        self.assertIn('paused', res.text.lower())
        # the login page is NEVER gated (owner must be able to get in/out)
        login = self.url_open('/web/login')
        self.assertEqual(login.status_code, 200)
        self.assertNotIn('paused', login.text.lower())

    def test_active_tenant_not_blocked(self):
        _set_status(self.env, 'active')
        self.authenticate('admin', 'admin')
        res = self.url_open('/odoo')
        self.assertEqual(res.status_code, 200)
        self.assertNotIn('your workspace is paused', res.text.lower())
