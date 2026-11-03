# -*- coding: utf-8 -*-
"""Option A: Public API client administration stays platform-only. An API client "acts as" a chosen user, and its
secret is only ever issued by server code; letting a tenant Owner create clients would let them bind a token to a
more privileged user (e.g. the platform entry proxy). The Owner is refused at the ORM."""
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestApiAdministrationIsPlatformOnly(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = new_test_user(cls.env, login='api_owner',
                                  groups='base.group_user,ncollection_core.group_role_owner')
        cls.service = new_test_user(cls.env, login='api_service', groups='base.group_user')

    def test_the_owner_cannot_create_or_read_api_clients(self):
        Client = self.env['ncollection.api.client'].with_user(self.owner)
        with self.assertRaises(AccessError):
            Client.create({'name': 'Owner client', 'user_id': self.service.id})
        with self.assertRaises(AccessError):
            Client.search([])

    def test_the_platform_still_administers_them(self):
        client = self.env['ncollection.api.client'].create({'name': 'Platform client', 'user_id': self.service.id})
        self.assertTrue(client.client_id)
