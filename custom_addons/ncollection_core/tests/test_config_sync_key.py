# -*- coding: utf-8 -*-
"""Tenant-side config-sync credential installer (#221).

Every assertion here goes through Odoo's REAL apikey verification
(``res.users.apikeys._check_credentials``) rather than inspecting the stored
column. That is deliberate: ``KEY_CRYPT_CONTEXT.hash`` is salted, so the stored
bytes differ on every write even for an identical key. A test that compared
hashes would fail on a correct re-key and pass on a broken one that happened to
write the same string — it would be measuring the wrong thing entirely.
"""

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.ncollection_core.models.config_sync_key import (
    KEY_NAME, SERVICE_LOGIN, SERVICE_XMLID)


@tagged('post_install', '-at_install')
class TestConfigSyncKey(TransactionCase):

    def setUp(self):
        super().setUp()
        self.installer = self.env['ncollection.config.sync.key']

    # ---- helpers ---------------------------------------------------------

    def _authenticates(self, raw_key):
        """True when Odoo would accept `raw_key` as an API key in this DB.

        ``scope='rpc'`` is not arbitrary: ``_check_apikey_credentials`` asserts a
        truthy scope from the CALLER, and the bearer path in res_users passes
        ``scope or 'rpc'``. The row we write stores ``scope=NULL``, which the
        lookup treats as "matches any scope" (``scope IS NULL OR scope = %s``).
        So this is the exact query the real config-sync bearer takes.
        """
        uid = self.env['res.users.apikeys']._check_credentials(
            scope='rpc', key=raw_key)
        return bool(uid)

    def _key_rows(self):
        user = self.env['res.users'].sudo().search(
            [('login', '=', SERVICE_LOGIN)], limit=1)
        if not user:
            return 0
        self.env.cr.execute(
            "SELECT COUNT(*) FROM res_users_apikeys WHERE user_id=%s AND name=%s",
            (user.id, KEY_NAME))
        return self.env.cr.fetchone()[0]

    # ---- the refusal cases ----------------------------------------------

    def test_an_empty_key_is_refused(self):
        """Writing an empty credential would be worse than writing nothing: the
        row would exist, so a caller would read success, while nothing could
        authenticate against it."""
        with self.assertRaises(ValueError):
            self.installer._install_key('', create_user=True)

    def test_a_too_short_key_is_refused_with_a_readable_message(self):
        """``res_users_apikeys`` carries CHECK (char_length(index) = 8) and index
        is ``key[:8]``. Without this guard a short key fails deep in Postgres,
        inside a subprocess whose only output channel is a marker line — the
        operator would see 'REKEY_ERR <constraint noise>' and have to go digging.
        Real derived keys are 44 chars, so this only ever fires on a
        misconfiguration, which is exactly when a clear message is worth most.
        """
        with self.assertRaises(ValueError) as ctx:
            self.installer._install_key('short', create_user=True)
        self.assertIn('too short', str(ctx.exception))

    def test_rekey_will_not_conjure_a_service_account(self):
        """A tenant with no config-sync account predates P2-T03.

        Creating one during a *maintenance* job would silently grant the platform
        a writable account in a tenant that never had one — a privilege change
        wearing the costume of a key rotation. The re-key path passes
        create_user=False and must no-op instead.
        """
        existing = self.env['res.users'].sudo().search(
            [('login', '=', SERVICE_LOGIN)])
        existing.unlink()
        user = self.installer._install_key('long-enough-key', create_user=False)
        self.assertFalse(user, "re-key created a service account it should not have")
        self.assertEqual(self._key_rows(), 0)

    # ---- the write ------------------------------------------------------

    def test_provisioning_creates_the_account_and_a_working_key(self):
        raw = 'provisioned-key-abc123'
        user = self.installer._install_key(raw, create_user=True)
        self.assertTrue(user)
        self.assertEqual(user.login, SERVICE_LOGIN)
        self.assertIn(self.env.ref('ncollection_core.group_config_sync'),
                      user.all_group_ids)
        self.assertTrue(self._authenticates(raw))

    def test_reinstalling_the_same_key_is_idempotent(self):
        """Rule 12: run twice, and the second run must be a no-op in EFFECT.

        Not in bytes — the salt guarantees a different hash. What must hold is
        that exactly one row exists and the same credential still authenticates.
        """
        raw = 'same-key-every-time'
        self.installer._install_key(raw, create_user=True)
        self.assertEqual(self._key_rows(), 1)
        self.installer._install_key(raw, create_user=True)
        self.assertEqual(self._key_rows(), 1, "re-run left a duplicate row")
        self.assertTrue(self._authenticates(raw))

    def test_rekeying_actually_revokes_the_old_key(self):
        """The whole point of #221, stated as a test.

        A rotation that leaves the previous key working is not a rotation. This
        is why the write is DELETE-then-INSERT rather than an UPDATE: nothing
        constrains (user_id, name) to be unique, so an UPDATE that missed a row
        would leave the old credential live and the failure would be silent.
        """
        old, new = 'the-leaked-key', 'the-replacement-key'
        self.installer._install_key(old, create_user=True)
        self.assertTrue(self._authenticates(old))

        self.installer._install_key(new, create_user=True)

        self.assertTrue(self._authenticates(new))
        self.assertFalse(self._authenticates(old),
                         "the OLD key still authenticates after a re-key")
        self.assertEqual(self._key_rows(), 1)

    # ---- identity is anchored, not looked up by a mutable string ---------

    def test_a_renamed_login_cannot_redirect_the_platform_credential(self):
        """`login` is mutable by anyone with res.users write access in the
        tenant. Resolving the service account by it meant the platform would
        install its own bearer — and the config-sync group — onto whichever
        account happened to hold that string. This pins the xmlid anchor.
        """
        self.installer._install_key('original-key-value', create_user=True)
        real = self.env.ref(SERVICE_XMLID)

        # Someone moves the login onto a different account. res_users_login_key
        # is UNIQUE, so the rename must reach the DB before the new row can claim
        # the string — flush, or the INSERT collides with the still-cached old
        # value. (That constraint is also why this takes two writes, not one.)
        real.sudo().write({'login': 'retired-config-sync@example.com'})
        self.env.flush_all()
        impostor = self.env['res.users'].sudo().create({
            'name': 'Impostor', 'login': SERVICE_LOGIN,
            'password': 'irrelevant-but-set',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })

        got = self.installer._install_key('replacement-key-value',
                                          create_user=False)

        self.assertEqual(got, real, "re-key followed the login, not the anchor")
        self.assertNotIn(self.env.ref('ncollection_core.group_config_sync'),
                         impostor.all_group_ids,
                         "the impostor was granted the config-sync group")

    def test_an_overprivileged_service_account_is_refused(self):
        """If the anchored account has picked up rights beyond its minimum,
        something changed it. Installing a live platform bearer on an account we
        no longer recognise turns a local misconfiguration into a durable
        credential — refuse and make a human look."""
        self.installer._install_key('original-key-value', create_user=True)
        user = self.env.ref(SERVICE_XMLID)
        user.sudo().write(
            {'group_ids': [(4, self.env.ref('base.group_system').id)]})

        with self.assertRaises(ValueError) as ctx:
            self.installer._install_key('replacement-key-value')
        self.assertIn('unexpected groups', str(ctx.exception))

    def test_revocation_clears_every_key_on_the_service_account(self):
        """A revocation scoped by key NAME would leave a stray row under any
        other name still authenticating as the service account — which is
        exactly what a 'revoke the leaked key' operation must not do."""
        self.installer._install_key('original-key-value', create_user=True)
        user = self.env.ref(SERVICE_XMLID)
        stray = 'a-stray-second-key'
        from odoo.addons.base.models.res_users import (
            KEY_CRYPT_CONTEXT, INDEX_SIZE)
        self.env.cr.execute(
            "INSERT INTO res_users_apikeys "
            "(name,user_id,scope,index,key,create_date) "
            "VALUES (%s,%s,NULL,%s,%s, now())",
            ('some-other-key', user.id, stray[:INDEX_SIZE],
             KEY_CRYPT_CONTEXT.hash(stray)))
        self.assertTrue(self._authenticates(stray))

        self.installer._install_key('replacement-key-value')

        self.assertFalse(self._authenticates(stray),
                         "a differently-named key survived the revocation")
        self.assertTrue(self._authenticates('replacement-key-value'))

    def test_an_existing_account_regains_the_group(self):
        """Idempotent repair: a role sync or a manual edit could drop the group,
        which would leave the key valid but every config push 403-ing."""
        self.installer._install_key('first-key-value', create_user=True)
        user = self.env['res.users'].sudo().search(
            [('login', '=', SERVICE_LOGIN)], limit=1)
        group = self.env.ref('ncollection_core.group_config_sync')
        user.write({'group_ids': [(3, group.id)]})
        self.assertNotIn(group, user.all_group_ids)

        self.installer._install_key('second-key-value', create_user=False)

        self.assertIn(group, user.all_group_ids)
