# -*- coding: utf-8 -*-
"""The shipped plans are product data; the sample tenants and subscriptions are demo data.

Measured in production: ``data/demo_data.xml`` was listed under ``data``, so a platform installed WITHOUT demo data
still got three sample tenants and their subscriptions, and every record in it was ``noupdate`` with Odoo's default
``forcecreate``: after an operator deleted one, the next ``-u ncollection_subscription`` would have created it again
(``odoo/tools/convert.py``: a missing noupdate record is created on update unless ``forcecreate="0"``).
"""
import ast
import os

from odoo.modules.module import get_module_path
from odoo.tests import TransactionCase, tagged
from odoo.tools import convert_file

MODULE = 'ncollection_subscription'


@tagged('post_install', '-at_install')
class TestShippedPlansFixture(TransactionCase):

    def _manifest(self):
        with open(os.path.join(get_module_path(MODULE), '__manifest__.py'), encoding='utf-8') as handle:
            return ast.literal_eval(handle.read())

    def test_an_upgrade_never_recreates_a_shipped_plan_an_operator_deleted(self):
        starter = self.env.ref(MODULE + '.demo_plan_starter')
        growth = self.env.ref(MODULE + '.demo_plan_growth')
        enterprise = self.env.ref(MODULE + '.demo_plan_enterprise')
        (starter | growth).unlink()
        # the plans' data file, loaded the way `-u ncollection_subscription` loads it
        convert_file(self.env, MODULE, 'data/demo_data.xml', {}, mode='update', noupdate=False)
        self.assertFalse(self.env.ref(MODULE + '.demo_plan_starter', raise_if_not_found=False), 'Starter came back')
        self.assertFalse(self.env.ref(MODULE + '.demo_plan_growth', raise_if_not_found=False), 'Growth came back')
        self.assertEqual(self.env.ref(MODULE + '.demo_plan_enterprise'), enterprise, 'the kept plan is untouched')
        self.assertFalse(self.env['ncollection.subscription.plan'].search([('code', 'in', ['STARTER', 'GROWTH'])]))

    def test_the_sample_tenants_and_subscriptions_are_demo_data_only(self):
        manifest = self._manifest()
        self.assertIn('data/demo_data.xml', manifest['data'], 'the shipped plans stay product data')
        self.assertEqual(manifest.get('demo'), ['demo/demo_tenants.xml'])
        with open(os.path.join(get_module_path(MODULE), 'data', 'demo_data.xml'), encoding='utf-8') as handle:
            shipped = handle.read()
        self.assertNotIn('model="ncollection.tenant"', shipped)
        self.assertNotIn('model="ncollection.subscription"', shipped)
        self.assertEqual(shipped.count('model="ncollection.subscription.plan" forcecreate="0"'), 3,
                         'each shipped plan opts out of re-creation')
