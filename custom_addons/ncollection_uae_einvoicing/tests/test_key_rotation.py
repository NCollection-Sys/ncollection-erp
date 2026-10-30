# -*- coding: utf-8 -*-
"""Credential key rotation (R48): versioned masters from the environment, atomic per-credential re-encryption.

The masters here are random per run (no secret literal in the source)."""

import logging
import os
import secrets
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from ..lib import secret_crypto as sc
from .common import GROUP, EInvoiceCase

OLD, NEW, OTHER = (secrets.token_hex(24) for _ in range(3))
VALUE = 'ASP-TOKEN-' + secrets.token_hex(8)


def env(current, version, previous=None, previous_version=None):
    values = {sc.MASTER_ENV: current, sc.VERSION_ENV: str(version), sc.PREVIOUS_ENV: previous or '',
              sc.PREVIOUS_VERSION_ENV: str(previous_version or '')}
    return patch.dict(os.environ, values)


@tagged('post_install', '-at_install')
class TestKeyRing(EInvoiceCase):

    def test_the_previous_master_is_optional_and_must_differ(self):
        with env(NEW, 2):
            self.assertEqual(sc.keyring_from_env(), (2, {2: NEW.encode()}))
        with env(NEW, 2, OLD, 1):
            self.assertEqual(sc.keyring_from_env(), (2, {2: NEW.encode(), 1: OLD.encode()}))
        for bad in ((NEW, 2, OLD, 2), (NEW, 2, NEW, 1), (NEW, 2, OLD, None), (NEW, 2, 'short', 1)):
            with env(*bad), self.assertRaises(sc.SecretError):
                sc.keyring_from_env()
        with patch.dict(os.environ, {sc.MASTER_ENV: NEW, sc.VERSION_ENV: 'zero'}), self.assertRaises(sc.SecretError):
            sc.keyring_from_env()
        with patch.dict(os.environ, {sc.MASTER_ENV: NEW, sc.VERSION_ENV: '0'}), self.assertRaises(sc.SecretError):
            sc.keyring_from_env()

    def test_the_default_version_is_one_so_existing_ciphertexts_keep_working(self):
        with patch.dict(os.environ, {sc.MASTER_ENV: OLD, sc.VERSION_ENV: '', sc.PREVIOUS_ENV: ''}):
            self.assertEqual(sc.keyring_from_env()[0], 1)


@tagged('post_install', '-at_install')
class TestKeyRotation(EInvoiceCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env['ncollection.einvoice.provider'].create(
            {'name': 'Rot ASP', 'company_id': cls.company.id})
        cls.other_company = cls._company('Other Tenant Co', tin='1876543219', trn='187654321902003',
                                         registry='112345678900098')
        cls.other_provider = cls.env['ncollection.einvoice.provider'].create(
            {'name': 'Other ASP', 'company_id': cls.other_company.id})
        cls.manager = cls._user('rot_manager', [GROUP + 'group_einvoice_manager'])
        cls.service = cls._user('rot_service', [GROUP + 'group_einvoice_service'])
        cls.clerk = cls._user('rot_clerk', [GROUP + 'group_einvoice_user'])
        cls.Secret = cls.env['ncollection.einvoice.secret']

    def _set(self, provider=None, name='api_token', value=VALUE):
        return self.Secret.with_user(self.manager).with_company(self.company).set_secret(
            provider or self.provider, name, value)

    def test_old_ciphertext_migrates_to_the_new_key(self):
        with env(OLD, 1):
            record = self._set()
        before = record.with_user(self.service).ciphertext
        self.assertEqual(record.key_version, 1)
        with env(NEW, 2, OLD, 1):
            self.assertEqual(record.with_user(self.service)._reveal(), VALUE, 'readable during the rotation')
            result = self.Secret.with_user(self.manager).action_rotate_keys()
            self.assertEqual((result['rotated'], result['failed'], result['current_version']), (1, 0, 2))
            self.assertEqual(record.key_version, 2)
            self.assertEqual(record.rotated_by, self.manager, 'the rotation is attributed')
            self.assertTrue(record.rotated_at)
            self.assertNotEqual(record.with_user(self.service).ciphertext, before)
            self.assertEqual(record.with_user(self.service)._reveal(), VALUE)
        with env(NEW, 2):  # the previous master is removed after the rotation: still readable
            self.assertEqual(record.with_user(self.service)._reveal(), VALUE)
            again = self.Secret.with_user(self.manager).action_rotate_keys()
            self.assertEqual((again['rotated'], again['already_current']), (0, 1), 'idempotent')

    def test_a_rotated_ciphertext_does_not_open_with_the_old_key(self):
        with env(OLD, 1):
            record = self._set()
        with env(NEW, 2, OLD, 1):
            self.Secret.with_user(self.manager).action_rotate_keys()
            blob = record.with_user(self.service).ciphertext
        context = dict(db=self.env.cr.dbname, company_id=self.company.id, provider=str(self.provider.id),
                       name='api_token')
        for master, version in ((OLD.encode(), 2), (OLD.encode(), 1), (OTHER.encode(), 2), (NEW.encode(), 1)):
            with self.assertRaises(sc.SecretError):
                sc.decrypt(master, blob, version=version, **context)

    def test_a_missing_master_leaves_the_old_secret_untouched(self):
        with env(OLD, 1):
            record = self._set()
        before = record.with_user(self.service).ciphertext
        with env(NEW, 2):  # the operator forgot the previous master
            result = self.Secret.with_user(self.manager).action_rotate_keys()
            self.assertEqual((result['rotated'], result['failed']), (0, 1))
            with self.assertRaises(UserError):
                record.with_user(self.service)._reveal()
        self.assertEqual((record.key_version, record.with_user(self.service).ciphertext), (1, before))
        with env(NEW, 2, OLD, 1):  # retried with the previous master present: it now migrates
            self.assertEqual(self.Secret.with_user(self.manager).action_rotate_keys()['rotated'], 1)
            self.assertEqual(record.with_user(self.service)._reveal(), VALUE)

    def test_a_tampered_ciphertext_fails_alone_and_the_others_rotate(self):
        with env(OLD, 1):
            good = self._set(name='good')
            bad = self._set(name='bad')
        bad.sudo().write({'ciphertext': good.sudo().ciphertext})  # moved: its AAD names another secret
        with env(NEW, 2, OLD, 1):
            result = self.Secret.with_user(self.manager).action_rotate_keys()
        self.assertEqual((result['rotated'], result['failed']), (1, 1))
        self.assertEqual((good.key_version, bad.key_version), (2, 1))

    def test_rotation_stays_inside_the_managers_companies(self):
        with env(OLD, 1):
            ours = self._set()
            theirs = self.Secret.sudo().create({'company_id': self.other_company.id,
                                                'provider_id': self.other_provider.id, 'secret_name': 'api_token',
                                                'ciphertext': sc.encrypt(OLD.encode(), VALUE, db=self.env.cr.dbname,
                                                                         company_id=self.other_company.id,
                                                                         provider=str(self.other_provider.id),
                                                                         name='api_token')})
        with env(NEW, 2, OLD, 1):
            self.Secret.with_user(self.manager).with_company(self.company).action_rotate_keys()
        self.assertEqual((ours.key_version, theirs.key_version), (2, 1), "another company's credential is untouched")

    def test_only_a_manager_rotates(self):
        for who in (self.clerk, self.service):
            with env(NEW, 2, OLD, 1), self.assertRaises(AccessError):
                self.Secret.with_user(who).action_rotate_keys()

    def test_rotation_logs_and_returns_no_secret(self):
        with env(OLD, 1):
            self._set()
        with env(NEW, 2, OLD, 1), self.assertLogs(level='DEBUG') as logs:
            result = self.Secret.with_user(self.manager).action_rotate_keys()
            logging.getLogger('odoo.test').debug('marker')
        text = ' '.join(logs.output) + repr(result)
        # the plaintext and both masters never appear. (Odoo's own DEBUG SQL log may show the new CIPHERTEXT as a
        # query parameter: that is the value the database stores anyway, useless without the master.)
        for forbidden in (VALUE, OLD, NEW):
            self.assertNotIn(forbidden, text)
        self.assertNotIn('ciphertext', repr(result))

    def test_a_new_secret_is_written_with_the_current_version(self):
        with env(NEW, 3, OLD, 2):
            record = self._set(name='fresh')
            self.assertEqual(record.key_version, 3)
            self.assertEqual(record.with_user(self.service)._reveal(), VALUE)


@tagged('post_install', '-at_install')
class TestCredentialWizard(EInvoiceCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env['ncollection.einvoice.provider'].create(
            {'name': 'Wiz ASP', 'company_id': cls.company.id})
        cls.manager = cls._user('wiz_manager', [GROUP + 'group_einvoice_manager'])
        cls.clerk = cls._user('wiz_clerk', [GROUP + 'group_einvoice_user'])

    def test_the_wizard_encrypts_and_stores_nothing_in_clear(self):
        with env(NEW, 1):
            wizard = self.env['ncollection.einvoice.secret.wizard'].with_user(self.manager).create(
                {'provider_id': self.provider.id, 'secret_name': 'api_token', 'value': VALUE})
        self.assertFalse(wizard.sudo().value)
        self.env.cr.execute('SELECT value FROM ncollection_einvoice_secret_wizard WHERE id = %s', (wizard.id,))
        self.assertIn(self.env.cr.fetchone()[0], (None, ''))
        secret = self.env['ncollection.einvoice.secret'].search([('provider_id', '=', self.provider.id)])
        self.assertTrue(secret.configured)
        self.assertNotIn(VALUE, secret.sudo().ciphertext)

    def test_only_a_manager_uses_the_wizard(self):
        with env(NEW, 1), self.assertRaises(AccessError):
            self.env['ncollection.einvoice.secret.wizard'].with_user(self.clerk).create(
                {'provider_id': self.provider.id, 'secret_name': 'api_token', 'value': VALUE})
