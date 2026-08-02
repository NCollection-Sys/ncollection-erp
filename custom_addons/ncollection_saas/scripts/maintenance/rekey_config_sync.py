# -*- coding: utf-8 -*-
# pylint: disable=print-used
# print IS the interface here, not debug output: this script runs inside an
# `odoo shell` subprocess and its STDOUT is the only channel back to the
# platform, which parses these lines. A logger would write into the TENANT's
# Odoo log, where nothing reads it, and the caller would see no result at all.
"""Config-sync re-key script (#221) — executed by `odoo shell -d <tenant_db>` in
an ISOLATED subprocess from the platform (never a cross-DB ORM/SQL call, Rule 3).

Rewrites ONLY this tenant's config-sync bearer. Deliberately NOT a re-seed:
``seed_tenant.py`` also renames the admin, resets their password, rewrites the
company name and the workspace config. Running that to rotate a key would be a
destructive way to change one row — the #212 security review said so explicitly,
which is why this script exists.

Reads ``NC_CONFIG_SYNC_TENANT_KEY`` — the ALREADY-DERIVED per-tenant key. The
platform master never enters this subprocess (#212).

Prints exactly one machine-readable marker on the last line, which the platform
parses to decide the per-tenant outcome:

  REKEY_OK                  key installed (or replaced)
  REKEY_SKIPPED_NO_ACCOUNT  no config-sync service account in this DB — a tenant
                            that predates P2-T03. NOT an error, and NOT something
                            to fix here: creating the account would silently grant
                            the platform write access to a tenant that never had
                            it. Provision it deliberately instead.
  REKEY_SKIPPED_NO_MODULE   ncollection_core absent — nothing to re-key against.
  REKEY_ERR <detail>        anything else.

`env` is provided by the odoo shell runtime.
"""
import os

_key = os.environ.get('NC_CONFIG_SYNC_TENANT_KEY')

try:
    if not _key:
        # Refuse loudly rather than write an empty credential. A silent no-op here
        # would report a "successful" rotation that left the old key working.
        print('REKEY_ERR NC_CONFIG_SYNC_TENANT_KEY not provided')
    elif 'ncollection.config.sync.key' not in env:  # noqa: F821
        print('REKEY_SKIPPED_NO_MODULE')
    else:
        _installer = env['ncollection.config.sync.key']  # noqa: F821
        _user = _installer._install_key(_key, create_user=False)
        if _user:
            env.cr.commit()  # noqa: F821
            print('REKEY_OK')
        else:
            print('REKEY_SKIPPED_NO_ACCOUNT')
except Exception as exc:  # noqa: BLE001 - the marker IS the error channel
    # Roll back so a half-applied write cannot leave the tenant with neither the
    # old nor the new key — that would lock config-sync out permanently, which is
    # strictly worse than the stale key we started with.
    env.cr.rollback()  # noqa: F821
    # Bounded on purpose. This string crosses into the platform's log AND the
    # tenant's chatter. Nothing today can put key material in it (the length
    # guard pre-empts the only CHECK constraint, and res_users_apikeys has no
    # UNIQUE constraint whose violation DETAIL could echo an index fragment) —
    # but a future schema change could, silently, with no test watching. The
    # class name plus a truncated message keeps it debuggable without an
    # unbounded channel out of the tenant.
    print('REKEY_ERR %s: %.200s' % (type(exc).__name__, exc))
