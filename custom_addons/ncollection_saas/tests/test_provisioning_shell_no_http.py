# -*- coding: utf-8 -*-
"""Every ``odoo shell`` step of provisioning runs with ``--no-http``.

Measured in production: the runner's config file had ``workers = 4``. The ``-i`` step passed ``--no-http`` and
succeeded, but the localization check (an ``odoo shell``) did not, started a prefork server on :8069 - which the
runner itself holds - and failed with ``OSError: [Errno 98] Address already in use``; the engine rolled the new
database back. A shell step must never start a server, whatever the config file says.
"""
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestProvisioningShellNoHttp(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tenant = cls.env['ncollection.tenant'].create({
            'company_name': 'Shell Co', 'database_name': 'shellco', 'email': 'owner@shellco.example',
            'status': 'trial', 'country_id': cls.env.ref('base.ae').id,
        })
        cls.job = cls.env['ncollection.provisioning.job'].create({
            'tenant_id': cls.tenant.id, 'database_name': 'shellco', 'status': 'queued',
        })

    def _commands(self, step):
        seen = []

        def fake_run(_self, cmd, label, stdin=None, env=None, timeout=None):
            seen.append(cmd)
            return ''

        with patch.object(type(self.job), '_run_odoo_subprocess', new=fake_run):
            step()
        return seen

    def test_the_localization_check_never_starts_a_server(self):
        self.assertTrue(self.tenant._nc_localization_package(), 'the UAE tenant has a package to verify')
        commands = self._commands(lambda: self.job._verify_localization('shellco'))
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][:2], ['odoo', 'shell'])
        self.assertIn('--no-http', commands[0])

    def test_the_seed_never_starts_a_server(self):
        commands = self._commands(lambda: self.job._seed_tenant('shellco'))
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][:2], ['odoo', 'shell'])
        self.assertIn('--no-http', commands[0])
