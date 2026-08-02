# -*- coding: utf-8 -*-
"""Config-sync key re-key / rotation (#221, follow-up to #212).

#212 made each tenant's config-sync bearer a per-tenant HMAC derivation of the
platform master. It wrote that key ONCE, at provisioning. So:

  * a tenant provisioned BEFORE #212 stores ``hash(raw master)`` while the
    platform now presents ``derive_tenant_key(master, db)`` — the push gets 401
    forever, and 401 is a soft, logged failure the nightly reconcile can never
    heal (the mismatch is permanent, not transient);
  * **rotating the master breaks every tenant at once**, for the same reason.

Because config-sync is what propagates ``action_suspend`` / ``action_expire`` /
plan downgrades into each tenant's ``ncollection.workspace.config`` — which P1-T10
license enforcement reads — a stale key means a suspended subscription silently
fails to lock the workspace. This module is the missing re-key path.

**Not a re-seed.** ``seed_tenant.py`` also renames the admin, resets their
password and rewrites the company name; #212's security review called using it
for rotation too heavy. The tenant-side script here writes ONE row.

**Isolation (Rule 3).** The platform never opens an ORM or SQL cursor on a tenant
DB. Each re-key runs in an isolated ``odoo shell`` subprocess, exactly like
provisioning's seed, reusing ``ncollection.saas.subprocess.mixin``.

**Why this model does not inherit that mixin.** ``ncollection.tenant`` is the most
cross-cutting model in the platform (subscription, config-sync, backup, domain,
fleet migration and provisioning all extend it) and its ``_inherit`` here is a bare
string. Turning that into a list to add a mixin requires an explicit ``_name`` or
the class silently registers as a different model — the trap documented in #243.
The mixin is an AbstractModel, so reaching it through ``self.env`` gets the same
helpers with no inheritance change on shared, shipped code (Rule 4).
"""

import logging
import os

from odoo import models
from odoo.exceptions import UserError

from .config_sync import _SYNC_KEY_ENV, _TENANT_KEY_ENV, derive_tenant_key

_logger = logging.getLogger(__name__)

REKEY_SCRIPT = os.path.join(
    os.path.dirname(__file__), '..', 'scripts', 'maintenance',
    'rekey_config_sync.py')

_MIXIN = 'ncollection.saas.subprocess.mixin'

# Markers the tenant-side script prints. Parsed rather than inferred from the
# exit code: the script exits 0 for "skipped" as well as "ok", because a tenant
# that predates config-sync is not a failure to alarm on.
_OK = 'REKEY_OK'
_SKIP_PREFIX = 'REKEY_SKIPPED'
_ERR_PREFIX = 'REKEY_ERR'

# Per-tenant outcomes. THREE, not a bool: a skip is a documented, benign state
# (a tenant that predates config-sync), and folding it into "failed" turns every
# rotation into a false alarm for the exact population the skip exists to handle.
_DONE = 'ok'
_SKIPPED = 'skipped'
_FAILED = 'failed'


class TenantConfigSyncRekey(models.Model):
    _inherit = 'ncollection.tenant'

    # ---- entry points ----------------------------------------------------

    def action_rekey_config_sync(self):
        """Re-key the selected tenants, then PROVE each one authenticates.

        Covers both variants #221 asks for, because they are the same operation
        at different cardinality: one record is the leaked-key response, every
        ready tenant is a master rotation. Nothing about the code path differs,
        so there is no second implementation to keep in step.

        Verification is not optional. A rotation that reports success without a
        push having authenticated is exactly the false-green this repo keeps
        getting bitten by — and here it would hide a fleet locked out of its own
        licence enforcement. After the key lands we run the ordinary config push
        and read the state #264 records.
        """
        self._rekey_assert_allowed()
        master = self._rekey_master()
        results = []
        for tenant in self:
            results.append(tenant._rekey_one(master))
        return self._rekey_summary(results)

    def action_rekey_config_sync_fleet(self):
        """Every ready tenant — the master-rotation entry point.

        Deliberately ignores `self`: an operator rotating the master must not
        silently re-key only whatever happened to be selected in the list view.
        """
        # Checked before the search, not just via the delegate below: otherwise a
        # non-admin learns whether any ready tenants exist from which error they
        # get back.
        self._rekey_assert_allowed()
        targets = self.search([
            ('database_status', '=', 'ready'),
            ('database_name', '!=', False),
        ])
        if not targets:
            raise UserError(self.env._("No ready tenants to re-key."))
        _logger.info("Config-sync fleet re-key starting for %s tenant(s)",
                     len(targets))
        return targets.action_rekey_config_sync()

    # ---- the operation ---------------------------------------------------

    def _rekey_assert_allowed(self):
        """Mirror the button's ``groups=`` at the ORM (Rule 4).

        A UI-only restriction is not a restriction: ``action_rekey_config_sync``
        is reachable over RPC by any authenticated user, and it spawns
        subprocesses against tenant databases. The button hides it; this refuses
        it.

        ``base.group_system`` is not a fresh judgement call — it is the group
        every other destructive tenant-touching operation in this layer already
        gates on (backup, restore, fleet migration, in
        ``security/ir.model.access.csv``). #245 tracks whether that whole layer
        should move to ``group_platform_admin``; deciding it here would fork the
        convention while that ticket is still open, so this follows the existing
        one and moves with it.
        """
        if not self.env.user.has_group('base.group_system'):
            raise UserError(self.env._(
                "Re-keying config-sync credentials requires Settings "
                "administrator rights."))

    def _rekey_master(self):
        """The platform master, or refuse.

        Refusing beats a silent no-op: without the master every tenant would be
        reported 'skipped' and an operator would read a clean summary as a
        completed rotation (the #219 lesson — a destructive misconfiguration must
        fail loudly, not quietly do nothing).
        """
        master = os.environ.get(_SYNC_KEY_ENV)
        if not master:
            raise UserError(self.env._(
                "%s is not set, so no per-tenant key can be derived. Set it in "
                "the secrets store / .env and retry — refusing rather than "
                "reporting a rotation that did not happen.", _SYNC_KEY_ENV))
        return master

    def _rekey_one(self, master):
        """Re-key ONE tenant. Returns ``(tenant, outcome, detail)``.

        ``outcome`` is one of OK / SKIPPED / FAILED — THREE states, not a
        boolean. A boolean collapsed SKIPPED into FAILED, and the tenant-side
        script's own docstring plus the runbook both say a skip is "NOT an
        error": it means a tenant that predates config-sync (no service account,
        or no ncollection_core). Reporting that as a failure meant an ERROR log
        line and a "FAILED" chatter entry on every rotation for every legacy
        tenant, and a sticky warning telling the operator those tenants "still
        present the OLD key" — which is false, they have no key at all. It also
        made the runbook's "green means done" contract unreachable for any fleet
        containing one legacy tenant, which is exactly the population the skip
        exists to handle. A summary that cries wolf every run is one operators
        learn to dismiss.

        Never raises: one unreachable tenant must not abort a fleet rotation
        half-way, leaving an operator unable to tell which tenants were done.
        """
        self.ensure_one()
        db = self.database_name
        mixin = self.env[_MIXIN]
        if self.database_status != 'ready' or not db:
            return (self, _SKIPPED, 'no ready database')
        try:
            mixin._assert_safe_db_name(db)      # injection / self-target guard
            out = self._rekey_subprocess(mixin, master, db)
            marker = self._rekey_marker(out)
            if marker.startswith(_SKIP_PREFIX):
                return (self, _SKIPPED, marker)
            if marker != _OK:
                return (self, _FAILED, marker)
            # The key is in. Now prove the platform can actually use it.
            return self._rekey_verify()
        except Exception as exc:                # noqa: BLE001 - reported per tenant
            _logger.error("Config-sync re-key failed for %s: %s", db, exc)
            return (self, _FAILED, '%s: %s' % (type(exc).__name__, exc))

    def _rekey_subprocess(self, mixin, master, db):
        """Run the tenant-side script in an isolated odoo shell.

        The MASTER is scrubbed from the subprocess environment and only the
        derived per-tenant key is passed in — the same contract provisioning
        uses (#212), so a tenant context never sees the platform secret.
        """
        with open(REKEY_SCRIPT, encoding='utf-8') as fh:
            script = fh.read()
        env_vars = os.environ.copy()
        env_vars.pop(_SYNC_KEY_ENV, None)
        env_vars[_TENANT_KEY_ENV] = derive_tenant_key(master, db)
        cmd = ['odoo', 'shell'] + mixin._odoo_conn_args(db) + ['--log-level=error']
        return mixin._run_odoo_subprocess(
            cmd, self.env._("config-sync re-key"), stdin=script, env=env_vars)

    @staticmethod
    def _rekey_marker(stdout):
        """The LAST marker line the script printed, or a synthetic error.

        Scans from the end: `odoo shell` may emit its own noise around our
        output, and taking the first match would let an earlier line win.
        """
        for line in reversed((stdout or '').splitlines()):
            line = line.strip()
            if line.startswith((_OK, _SKIP_PREFIX, _ERR_PREFIX)):
                return line
        return '%s no marker in subprocess output' % _ERR_PREFIX

    def _rekey_verify(self):
        """Push the real config and read the state #264 records.

        This is what makes the runbook's "confirm pushes authenticate" a step the
        job performs rather than an instruction a human might skip. A 401 here
        means the key did not take, and the tenant is reported failed even though
        the subprocess said OK.
        """
        self.sync_workspace_config()
        # Defensive rather than required: _config_sync_record writes through
        # self.sudo() on the SAME cursor, and the ORM cache is per-transaction,
        # so the value is already visible. Cheap insurance against that ever
        # changing, on the one read whose whole purpose is not being stale.
        self.invalidate_recordset(['config_sync_state', 'config_sync_last_error'])
        if self.config_sync_state == 'ok':
            return (self, _DONE, 'authenticated')
        return (self, _FAILED, 'key written but push still failing: %s' % (
            self.config_sync_last_error or self.config_sync_state or 'unknown'))

    # ---- reporting -------------------------------------------------------

    def _rekey_summary(self, results):
        """Per-tenant chatter + one operator-facing summary.

        The audit trail rides on ncollection.tenant's existing mail.thread (#264)
        — a key rotation is a security event and needs to be attributable, but it
        does not need a model and a table of its own.
        """
        done = [r for r in results if r[1] == _DONE]
        skipped = [r for r in results if r[1] == _SKIPPED]
        failed = [r for r in results if r[1] == _FAILED]

        for tenant, outcome, detail in results:
            tenant.message_post(body=self.env._(
                "Config-sync key re-key: %(outcome)s (%(detail)s)",
                outcome=outcome.upper(), detail=detail))
        # Only real failures reach ERROR. A skip at ERROR level would fire
        # P2-T10's log watcher on every rotation for every legacy tenant.
        for tenant, _outcome, detail in failed:
            _logger.error("Config-sync re-key FAILED for %s: %s",
                          tenant.database_name, detail)
        for tenant, _outcome, detail in skipped:
            _logger.info("Config-sync re-key skipped for %s: %s",
                         tenant.database_name, detail)
        _logger.info("Config-sync re-key finished: %s ok, %s skipped, %s failed",
                     len(done), len(skipped), len(failed))

        names = ', '.join(t.database_name or '?' for t, _o, _d in failed)
        skip_note = ''
        if skipped:
            skip_note = self.env._(
                " %(n)s tenant(s) skipped (no config-sync account — they predate "
                "it and hold no key to rotate): %(names)s.",
                n=len(skipped),
                names=', '.join(t.database_name or '?' for t, _o, _d in skipped))
        if failed:
            message = self.env._(
                "Re-keyed %(ok)s tenant(s); %(bad)s FAILED: %(names)s. Failed "
                "tenants still present the OLD key — check each one's chatter "
                "before considering the rotation complete.",
                ok=len(done), bad=len(failed), names=names) + skip_note
        else:
            message = self.env._(
                "Re-keyed %(ok)s tenant(s); every one authenticated on the "
                "verification push.", ok=len(done)) + skip_note
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': self.env._("Config-sync re-key"),
                'message': message,
                # Skips do not make a rotation "not done" — they are reported,
                # not alarmed. Only a real failure is sticky.
                'type': 'warning' if failed else 'success',
                'sticky': bool(failed),
            },
        }
