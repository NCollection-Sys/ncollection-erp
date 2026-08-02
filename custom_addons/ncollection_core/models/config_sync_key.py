# -*- coding: utf-8 -*-
"""Tenant-side config-sync credential (the receiving end of #212 / #221).

THE single definition of "install the config-sync bearer into this tenant".
Two callers, both running INSIDE a tenant DB in an isolated ``odoo`` subprocess:

  * ``seed_tenant.py``        — at provisioning (P2-T01);
  * ``rekey_config_sync.py``  — when the platform master rotates, or one tenant's
                                key leaks (#221).

They used to be one caller with the SQL inline. Keeping a second copy would put
a security-critical write in two places where it can silently diverge — the
exact class of bug #243 removed from the provisioning engine. One definition.

**Why this lives in ncollection_core and not ncollection_saas.** It runs in the
TENANT database. ncollection_core is in ``CORE_TENANT_MODULES``, so it is present
in every tenant; ncollection_saas is platform-only and is never installed in a
tenant. The platform half — deriving the key from the master — stays in
``ncollection_saas.config_sync.derive_tenant_key``. Only the DERIVED key ever
crosses into a tenant, never the master (#212).

**Only the hash is stored.** ``res_users_apikeys.key`` holds
``KEY_CRYPT_CONTEXT.hash(raw)``, so a dump of a tenant DB never yields a usable
credential, and a leaked key authenticates against this ONE tenant.
"""

import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

# The non-interactive account the platform authenticates as over json2. NOT a
# system admin — it carries group_config_sync, scoped to workspace.config writes.
SERVICE_LOGIN = 'config-sync@ncollection.internal'
SERVICE_NAME = 'Config Sync (platform)'
# res_users_apikeys.name — also the lookup key for replacing the row, so it must
# stay stable across provisioning and re-key.
KEY_NAME = 'config-sync'


class ConfigSyncKey(models.AbstractModel):
    _name = 'ncollection.config.sync.key'
    _description = 'Tenant-side config-sync credential installer'

    @api.model
    def _service_user(self, create=False):
        """The config-sync service account, or an empty recordset.

        ``create=False`` by default: a re-key must NOT conjure a service account
        into a tenant that never had one. That would silently turn "this tenant
        predates config-sync" into "this tenant now has a platform-writable
        account", which is a privilege change disguised as a maintenance job.
        Provisioning passes ``create=True`` because creating it IS its job.
        """
        user = self.env['res.users'].sudo().search(
            [('login', '=', SERVICE_LOGIN)], limit=1)
        group = self.env.ref('ncollection_core.group_config_sync')
        if user:
            # Idempotent re-assert: an existing account must still carry the
            # group, in case a role sync or a manual edit dropped it.
            user.write({'group_ids': [(4, group.id)]})
            return user
        if not create:
            return user
        # password: bearer-only account, but a NULL/known password would be an
        # unguarded login surface. Random and never disclosed.
        import secrets  # noqa: PLC0415 - only needed on the creation path
        return self.env['res.users'].sudo().create({
            'name': SERVICE_NAME,
            'login': SERVICE_LOGIN,
            'password': secrets.token_urlsafe(32),
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id, group.id])],
        })

    @api.model
    def _install_key(self, raw_key, create_user=False):
        """Replace this tenant's config-sync bearer with ``raw_key``.

        Returns the service user on success, or an empty recordset when there is
        no service account and ``create_user`` is False (the re-key case above).

        DELETE-then-INSERT rather than UPDATE: the row is keyed by
        ``(user_id, name)`` with no unique constraint, so an UPDATE could leave a
        second, still-valid stale row behind — i.e. the old key would keep
        authenticating after a rotation, which is the one thing a rotation exists
        to prevent.

        Idempotent: re-running with the same ``raw_key`` yields a DIFFERENT
        stored hash (the crypt context salts) but the SAME accepted credential.
        Assert on authentication, never on the stored bytes.
        """
        # Imported lazily: these are Odoo internals, and importing them at module
        # scope would couple every ncollection_core load to res_users' layout.
        from odoo.addons.base.models.res_users import (  # noqa: PLC0415
            KEY_CRYPT_CONTEXT, INDEX_SIZE)
        if not raw_key:
            raise ValueError('refusing to install an empty config-sync key')
        # res_users_apikeys carries CHECK (char_length(index) = 8), and `index` is
        # raw_key[:INDEX_SIZE]. A key shorter than that fails at the DB layer with
        # an opaque constraint error from inside a subprocess whose output is a
        # marker line — i.e. the operator would see "REKEY_ERR <postgres noise>".
        # Reject it here, where the message can say what is actually wrong.
        if len(raw_key) < INDEX_SIZE:
            raise ValueError(
                'config-sync key too short (%d chars, need at least %d)'
                % (len(raw_key), INDEX_SIZE))
        user = self._service_user(create=create_user)
        if not user:
            _logger.warning(
                'config-sync re-key skipped: no %s account in this database',
                SERVICE_LOGIN)
            return user
        self.env.cr.execute(
            "DELETE FROM res_users_apikeys WHERE user_id=%s AND name=%s",
            (user.id, KEY_NAME))
        self.env.cr.execute(
            "INSERT INTO res_users_apikeys (name,user_id,scope,index,key,create_date) "
            "VALUES (%s,%s,NULL,%s,%s, now())",
            (KEY_NAME, user.id, raw_key[:INDEX_SIZE],
             KEY_CRYPT_CONTEXT.hash(raw_key)))
        _logger.info('config-sync key installed for %s', SERVICE_LOGIN)
        return user
