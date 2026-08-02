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
import secrets

from odoo import api, models
# `base` is installed in every Odoo database, so this coupling exists whether the
# import sits here or inside the method — module scope just makes it visible.
# res_users_apikeys.key/index are deliberately NOT ORM fields and Odoo's own
# _generate() mints its own key rather than accepting one, so these constants
# (and the raw SQL below) are the only way to install an externally-derived key.
from odoo.addons.base.models.res_users import KEY_CRYPT_CONTEXT, INDEX_SIZE

_logger = logging.getLogger(__name__)

# The non-interactive account the platform authenticates as over json2. NOT a
# system admin — it carries group_config_sync, scoped to workspace.config writes.
SERVICE_LOGIN = 'config-sync@ncollection.internal'
SERVICE_NAME = 'Config Sync (platform)'
# The IMMUTABLE anchor for that account. login can be renamed by anyone with
# res.users write access in the tenant; an xmlid cannot be moved by editing a
# user record, so the platform's credential stays bound to the account it was
# issued for. Created on provisioning, and adopted for pre-existing accounts.
SERVICE_XMLID = 'ncollection_core.user_config_sync_service'
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

        **Identity is anchored to an ir.model.data xmlid, not to the login.**
        ``login`` is a mutable string that anyone with ``res.users`` write access
        inside the tenant can move between accounts. Resolving by login meant the
        platform would hand its credential — and the config-sync group — to
        whichever account happened to hold that string at that moment. Even with
        no attacker, an operator renaming the account silently redirects the
        platform's own key onto a different user and reports success.

        Accounts created before this anchor existed are ADOPTED on first sight:
        found by login once, then pinned by xmlid so a later call cannot be
        redirected.
        """
        user = self._resolve_service_user()
        group = self.env.ref('ncollection_core.group_config_sync')
        if user:
            self._assert_not_privileged(user, group)
            # Idempotent re-assert: an existing account must still carry the
            # group, in case a role sync or a manual edit dropped it.
            user.write({'group_ids': [(4, group.id)]})
            return user
        if not create:
            return user
        # password: bearer-only account, but a NULL/known password would be an
        # unguarded login surface. Random and never disclosed.
        user = self.env['res.users'].sudo().create({
            'name': SERVICE_NAME,
            'login': SERVICE_LOGIN,
            'password': secrets.token_urlsafe(32),
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id, group.id])],
        })
        self._pin_service_user(user)
        return user

    @api.model
    def _resolve_service_user(self):
        """xmlid first; login only as a one-time adoption path."""
        user = self.env.ref(SERVICE_XMLID, raise_if_not_found=False)
        if user:
            return user.sudo()
        user = self.env['res.users'].sudo().search(
            [('login', '=', SERVICE_LOGIN)], limit=1)
        if user:
            self._pin_service_user(user)
        return user

    @api.model
    def _pin_service_user(self, user):
        """Record the xmlid so identity stops depending on a mutable login."""
        module, name = SERVICE_XMLID.split('.')
        self.env['ir.model.data'].sudo().create({
            'module': module, 'name': name,
            'model': 'res.users', 'res_id': user.id, 'noupdate': True,
        })

    @api.model
    def _assert_not_privileged(self, user, group):
        """Refuse to install a platform credential on an over-privileged account.

        The service account is deliberately minimal: ``base.group_user`` plus
        ``group_config_sync``. If the account carrying our xmlid has picked up
        anything else, something changed it — a manual edit, a role sync gone
        wrong, or our identity being attached to a privileged user.

        Worth doing even though the realistic ways to reach this state need
        rights that could already write ``workspace.config`` directly: installing
        a live, platform-issued bearer onto an account we no longer recognise
        converts a local misconfiguration into a durable credential, and this
        ticket exists precisely so every config-sync credential is accounted for.
        """
        allowed = self.env.ref('base.group_user') | group
        # Direct assignments only. base.group_user implies a spread of technical
        # groups on a normal install; inherited ones are not evidence of tampering.
        unexpected = user.group_ids - allowed
        if unexpected:
            raise ValueError(
                'config-sync service account %s carries unexpected groups (%s); '
                'refusing to install a platform key on it'
                % (user.login, ', '.join(unexpected.mapped('name'))))

    @api.model
    def _install_key(self, raw_key, create_user=False):
        """Replace this tenant's config-sync bearer with ``raw_key``.

        Returns the service user on success, or an empty recordset when there is
        no service account and ``create_user`` is False (the re-key case above).

        DELETE-then-INSERT rather than UPDATE: nothing constrains
        ``(user_id, name)`` to be unique, so an UPDATE that missed a row would
        leave the old key authenticating after a rotation — the one thing a
        rotation exists to prevent.

        The DELETE clears EVERY api key on the service account, not only the row
        named ``config-sync``. This account is ours and is bearer-only, so it has
        no legitimate second key; scoping the delete by name meant a stray row
        under any other name would survive a "revoke the leaked key" rotation and
        keep authenticating as the service account. A revocation must be total or
        it is not a revocation.

        Idempotent: re-running with the same ``raw_key`` yields a DIFFERENT
        stored hash (the crypt context salts) but the SAME accepted credential.
        Assert on authentication, never on the stored bytes.
        """
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
            "DELETE FROM res_users_apikeys WHERE user_id=%s", (user.id,))
        self.env.cr.execute(
            "INSERT INTO res_users_apikeys (name,user_id,scope,index,key,create_date) "
            "VALUES (%s,%s,NULL,%s,%s, now())",
            (KEY_NAME, user.id, raw_key[:INDEX_SIZE],
             KEY_CRYPT_CONTEXT.hash(raw_key)))
        _logger.info('config-sync key installed for %s', SERVICE_LOGIN)
        return user
