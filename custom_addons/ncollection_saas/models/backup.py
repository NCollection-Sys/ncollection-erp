# -*- coding: utf-8 -*-
"""Tenant backup manager (P2-T05, ARCHITECTURE_DATA_PLATFORM §5.1).

The tenant-granular layer ON TOP of PITR (P2-T04): a nightly per-tenant
`pg_dump` + filestore tar, encrypted, with `ncollection.backup` records, a
retention pyramid (7 daily / 4 weekly / 12 monthly), failure alerting, and a
monthly restore drill. PITR covers cluster disaster recovery (RPO ~1 min); this
covers cheap per-tenant restore + long-term archival.

The heavy work runs OFF the HTTP workers: the crons enqueue jobs on the
provisioning queue channel, executed by the dedicated runner (P2-T01), which
shells out to scripts/backup/*.sh (pg_dump/tar/openssl) — never a cross-DB ORM
cursor (Rule 3).
"""

import logging
import os
import shutil
import subprocess

from odoo import api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

_BACKUP_SCRIPT = os.path.join(
    os.path.dirname(__file__), '..', 'scripts', 'backup', 'tenant_backup.sh')
_RESTORE_SCRIPT = os.path.join(
    os.path.dirname(__file__), '..', 'scripts', 'backup', 'tenant_restore.sh')

# Reuse the provisioning runner's channel so heavy backups never touch the HTTP
# workers (ARCHITECTURE_DATA_PLATFORM §10).
_BACKUP_CHANNEL = 'root.provisioning'
# The backup root, kept in lockstep with tenant_backup.sh's own
#   BACKUP_DIR="${NC_BACKUP_DIR:-/var/lib/odoo/backups}"
# and its `work="$BACKUP_DIR/$DB"` layout. Read from the SAME env var so the
# two cannot drift: if someone repoints the scripts, the guard follows (#288).
_BACKUP_ROOT_ENV = 'NC_BACKUP_DIR'
_DEFAULT_BACKUP_ROOT = '/var/lib/odoo/backups'

_SUBPROCESS_TIMEOUT = 60 * 60  # 1 h — a large tenant dump can be slow

# Retention pyramid (§5.1): keep the newest N of each type per tenant.
_RETENTION = {'daily': 7, 'weekly': 4, 'monthly': 12}


class NcollectionBackup(models.Model):
    _name = 'ncollection.backup'
    _description = 'Tenant Backup'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc, id desc'

    name = fields.Char(compute='_compute_name', store=True)
    tenant_id = fields.Many2one(
        'ncollection.tenant', required=True,
        ondelete='cascade', tracking=True)
    # SNAPSHOT, not related (#299). As `related='tenant_id.database_name'` it
    # FOLLOWED a tenant rename, so afterwards the guard computed this backup's
    # root from the NEW name while its files stayed under the OLD one -- every
    # historical backup silently unrestorable while looking healthy.
    #
    # Renaming is reachable: tenant.write() locks the name only while
    # provisioning/ready and deliberately leaves it writable in
    # not_provisioned/error, which is where a failed provisioning or fleet
    # migration puts a tenant.
    #
    # readonly is UI-only and unenforced over RPC (the #288 file_path lesson),
    # so this is DERIVED in create() and PINNED in write(). Both are needed:
    # deriving without pinning lets it be rewritten after the fact; pinning
    # without deriving just freezes a caller-supplied string.
    database_name = fields.Char(readonly=True, copy=False)
    backup_type = fields.Selection(
        selection=[('daily', 'Daily'), ('weekly', 'Weekly'), ('monthly', 'Monthly')],
        default='daily', required=True, tracking=True)
    status = fields.Selection(
        selection=[('pending', 'Pending'), ('running', 'Running'),
                   ('done', 'Done'), ('failed', 'Failed')],
        default='pending', required=True, tracking=True)
    # copy=False alongside database_name: a duplicate re-derives the snapshot
    # from the (possibly renamed) live tenant while carrying the old path
    # verbatim, producing a record whose file can never satisfy its own
    # ownership check. Better to copy nothing than to copy a lie (#299).
    file_path = fields.Char(string='Backup File', readonly=True, copy=False)
    file_size = fields.Integer(string='Size (bytes)', readonly=True)
    started_at = fields.Datetime(readonly=True)
    completed_at = fields.Datetime(readonly=True)
    error_log = fields.Text(readonly=True)

    @api.depends('database_name', 'backup_type', 'create_date')
    def _compute_name(self):
        for rec in self:
            stamp = fields.Datetime.to_string(rec.create_date or fields.Datetime.now())
            rec.name = '%s / %s / %s' % (
                rec.database_name or '?', rec.backup_type, stamp)

    # ---- engine ----------------------------------------------------------

    def action_run_sync(self):
        """Run inline (manual button / tests) — same engine, no queue."""
        for rec in self:
            rec.run_backup()
        return True

    def _enqueue(self):
        """Queue the backup on the provisioning channel (off HTTP workers)."""
        for rec in self:
            rec.with_delay(
                channel=_BACKUP_CHANNEL,
                description="Backup tenant '%s'" % rec.database_name,
                identity_key='nc-backup-%s' % rec.id,
            ).run_backup()

    def run_backup(self):
        """Produce the encrypted per-tenant bundle; record result or failure.

        Public so OCA queue_job can call it in the runner worker. Failure is
        recorded + alerted, not raised past the record — the nightly cron and
        the next run heal a transient miss."""
        self.ensure_one()
        if self.status in ('running', 'done'):
            return
        db = self.database_name
        if not db:
            self.write({'status': 'failed', 'error_log': 'Tenant has no database.'})
            return
        self.write({'status': 'running', 'started_at': fields.Datetime.now()})
        try:
            out = self._run_subprocess(['bash', _BACKUP_SCRIPT, db, self.backup_type])
            path = self._parse_result(out, 'RESULT_PATH')
            size = self._parse_result(out, 'RESULT_BYTES')
            self.write({
                'status': 'done',
                'file_path': path,
                'file_size': int(size) if size and size.isdigit() else 0,
                'completed_at': fields.Datetime.now(),
            })
        except Exception as exc:  # pylint: disable=broad-except
            _logger.warning("Backup failed for '%s': %s", db, exc)
            self.write({
                'status': 'failed',
                'error_log': str(exc)[-4000:],
                'completed_at': fields.Datetime.now(),
            })
            self._alert_failure()

    @api.model_create_multi
    def create(self, vals_list):
        """Stamp database_name from the tenant, ignoring any supplied value.

        Accepting one would make the snapshot caller-chosen, and the write()
        pin below would then merely freeze it -- the exact shape #288 closed
        on file_path.
        """
        Tenant = self.env['ncollection.tenant'].sudo()
        out = []
        for vals in vals_list:
            vals = dict(vals)               # never mutate the caller's dict
            tid = vals.get('tenant_id')
            # Normalise before browse(): a Many2one sent as an x2many command
            # tuple makes browse() raise a raw TypeError from inside create(),
            # which is a worse failure than Odoo's own field validation.
            tenant = Tenant.browse(tid) if isinstance(tid, int) else Tenant
            if not tenant or not tenant.database_name:
                # Refuse rather than stamp False. A backup with no snapshot can
                # never be taken (run_backup dumps `database_name`) nor
                # restored (the ownership guard has nothing to resolve), and it
                # was the ONLY state in which the write-pin's earlier truthy
                # check could be bypassed. Removing the state beats guarding
                # it. No legitimate caller reaches here: the nightly cron
                # filters ready + database_name != False, and fleet migration
                # snapshots a tenant that is already ready.
                raise ValidationError(self.env._(
                    "Cannot create a backup for a tenant with no database "
                    "name — it could never be taken or restored."))
            vals['database_name'] = tenant.database_name
            out.append(vals)
        return super().create(out)

    def write(self, vals):
        """A snapshot's provenance is immutable once set (#275).

        _assert_restore_target exempts "restoring over the backup's OWN
        tenant". That exemption is only as trustworthy as tenant_id, which was
        an ordinary writable Many2one -- and the caller needs write on this
        model anyway. So the guard was one write from useless:

            backup_of_A.write({'tenant_id': victim.id})
            backup_of_A.restore_to(victim.database_name)   # now "its own"

        Reassigning would also silently move `database_name` (related, stored),
        so nothing downstream could tell. Pinning tenant_id gives the exemption
        a fixed point to stand on.
        """
        if 'tenant_id' in vals:
            for rec in self:
                if rec.tenant_id and vals['tenant_id'] != rec.tenant_id.id:
                    raise ValidationError(self.env._(
                        "A backup's tenant cannot be reassigned. Snapshot "
                        "'%(b)s' belongs to '%(t)s'; repointing it would let a "
                        "restore claim another tenant's database as its own.",
                        b=rec.name or rec.id,
                        t=rec.tenant_id.database_name or rec.tenant_id.display_name))
        if 'database_name' in vals:
            # UNCONDITIONAL. The first version only raised when the current
            # snapshot was truthy -- and create() legitimately produces False
            # for a tenant that is not provisioned yet, so a backup on such a
            # tenant could have its snapshot written to ANY string, including a
            # live victim's real database name. Reproduced in review.
            #
            # The docstring promised "immutable once set" while the code
            # exempted exactly the state where nothing was set yet. A guard
            # that lies about its own guarantee is worse than an absent one:
            # the next reader trusts it, and _cron_restore_drill picks the
            # newest done backup across ALL tenants with no tenant filter.
            #
            # Nothing legitimate needs this: create() stamps vals BEFORE
            # super() and never routes through write().
            raise ValidationError(self.env._(
                "A backup's tenant database name is stamped at creation and "
                "can never be written (snapshot %r). It is what the restore "
                "guard resolves the file's directory from, so rewriting it "
                "would let a restore claim another tenant's dump.",
                self.database_name if len(self) == 1 else None))
        return super().write(vals)

    def _assert_restore_target(self, target_db):
        """Refuse a restore that would destroy the wrong database (#275).

        This method drives pg_terminate_backend -> dropdb -> createdb through
        tenant_restore.sh. It is the platform's most destructive primitive and
        it had NO guard: `restore_to` checked only that file_path was set, so
        one RPC could drop a live tenant and serve ANOTHER tenant's snapshot
        under its database name -- irreversible loss plus a cross-tenant
        isolation break, the invariant Rule 3 and ARCHITECTURE_SECURITY exist
        to protect.

        Every caller had its own guard and the method had none, so the guards
        were bypassable by not being a caller. The rule, in one line: a LIVE
        tenant database may be restored over ONLY by its own backup.

        Both halves of ownership are now checked: WHERE it lands
        (_assert_restore_target) and WHAT gets restored
        (_assert_file_belongs_to_tenant, #288). Pinning only the target left
        the identical blast radius one field over -- `file_path` is unenforced
        over RPC and untracked, and the cipher passphrase is platform-wide, so
        any tenant's dump decrypts.

        Two legitimate shapes, both preserved:
          * scratch/staging  -- the wizard and the nightly restore drill;
          * in-place rollback of the backup's OWN tenant (#244).
        """
        subproc = self.env['ncollection.saas.subprocess.mixin']
        own_db = self.tenant_id.sudo().database_name
        # sudo: the check must not depend on the CALLER's record rules --
        # invisibility must never read as permission. NOT independently tested,
        # and honestly so: the ACL on this model is base.group_system, which
        # sees every tenant, so today sudo() makes no observable difference.
        # It is defence against a future record rule, not a proven guard, and a
        # test asserting it would only be asserting the implementation.
        clashes = self.env['ncollection.tenant'].sudo().with_context(
            active_test=False).search_count([('database_name', '=', target_db)])
        if clashes and target_db != own_db:
            raise ValidationError(self.env._(
                "Refusing to restore backup of '%(src)s' over live tenant "
                "database '%(dst)s'. A live tenant database may only be "
                "restored over by its OWN backup; use a scratch name.",
                src=own_db or '?', dst=target_db))
        if target_db and target_db == own_db:
            # Our own tenant: hold it to the strict tenant rule, which
            # provisioning's _validate_db_name already enforced on the way in.
            subproc._assert_safe_db_name(target_db)
        else:
            # Scratch: '_' allowed purely so `drill_%s` and `restore_%s` keep
            # working. It buys NO isolation -- an underscored database is just
            # as reachable over HTTP (see SCRATCH_DB_NAME_RE).
            subproc._assert_scratch_db_name(target_db)

    def _backup_root(self):
        """The directory tenant_backup.sh writes into, read from the same env
        var so a redeployment cannot move one without the other."""
        return os.environ.get(_BACKUP_ROOT_ENV) or _DEFAULT_BACKUP_ROOT

    def _assert_file_belongs_to_tenant(self):
        """The dump about to be restored must be one THIS tenant produced.

        Binding the target (_assert_restore_target) without binding the file
        left the same cross-tenant restore one write away: `file_path` is a
        plain stored Char, its `readonly=True` is UI-only and unenforced over
        RPC, and it is not tracked -- so repointing it leaves no chatter trail
        either. With one platform-wide cipher passphrase, any tenant's .enc
        decrypts happily.

        tenant_backup.sh already lays backups out per tenant
        (`$BACKUP_DIR/$DB/<base>.tar.enc`), so ownership is checkable from the
        path alone. realpath() first: `..` segments and symlinks are exactly
        how a prefix test gets fooled.
        """
        # DELIBERATE ASYMMETRY, do not "simplify" (#299). This guard keys on
        # the SNAPSHOT while _assert_restore_target keys on the LIVE
        # tenant_id.database_name. Making both agree looks like a tidy-up and
        # is actually the vulnerability: after a rename frees a name and a NEW
        # tenant takes it, only the live-value check refuses a restore that
        # would destroy that new tenant's database. Audited explicitly.
        #
        # The SNAPSHOT (#299), not tenant_id.database_name: the live value
        # follows a rename, the files do not. Keying on the live value made
        # every historical backup of a renamed tenant unrestorable. The
        # snapshot is derived at create and pinned in write, so it is no
        # weaker a binding -- and unlike the live value, cannot be moved.
        db = self.database_name
        if not db:
            raise ValidationError(self.env._(
                "Backup '%s' has no tenant database, so the file it points at "
                "cannot be attributed to anyone. Refusing to restore it.",
                self.name or self.id))
        # Validate the name BEFORE using it as a path segment. The first
        # version of this trusted database_name verbatim, and that was a
        # CRITICAL: `_check_locked_database_name` only enforces the format
        # while database_status is 'provisioning' or 'ready', so a tenant in
        # 'not_provisioned' or 'error' can hold ANY string. `.` then makes
        # os.path.join(root, db) realpath straight back to the backup ROOT, so
        # every tenant's dump satisfies the prefix test; `..` reaches higher
        # still. Same check target_db gets a few lines up -- the value was
        # simply never run through it.
        #
        # Do NOT infer safety from lifecycle state. Check the value at the
        # point it is about to be trusted as a path.
        self.env['ncollection.saas.subprocess.mixin']._assert_safe_db_name(db)
        root = os.path.realpath(self._tenant_backup_dir(db))
        real = os.path.realpath(self.file_path)
        if real != root and not real.startswith(root + os.sep):
            raise ValidationError(self.env._(
                "Backup file %(f)s does not belong to tenant '%(db)s' "
                "(expected it under %(root)s). Restoring a dump this record "
                "did not produce would serve another tenant's data.",
                f=self.file_path, db=db, root=root))

    def restore_to(self, target_db):
        """Restore this backup into a scratch database, or in-place over this
        backup's OWN tenant. Public so the wizard + queue_job can call it --
        renaming it would break `with_delay(...).restore_to(...)`, since
        queue_job persists the method NAME. The guard lives inside for that
        reason: it then applies to every caller, including raw RPC. Returns
        the target."""
        self.ensure_one()
        if not self.file_path:
            raise RuntimeError("Backup has no file to restore.")
        self._assert_restore_target(target_db)
        self._assert_file_belongs_to_tenant()
        self._run_subprocess(['bash', _RESTORE_SCRIPT, self.file_path, target_db])
        return target_db

    def _run_subprocess(self, cmd):
        result = subprocess.run(
            cmd, env=self._script_env(), capture_output=True, text=True,
            timeout=_SUBPROCESS_TIMEOUT, check=False)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or '')[-2000:])
        return result.stdout or ''

    @api.model
    def _script_env(self):
        """Env for the backup/restore scripts: DB connection + the cipher pass
        (from the secrets store / .env — never hard-coded)."""
        env = os.environ.copy()
        env.setdefault('HOST', 'db')
        env.setdefault('USER', 'odoo')
        return env

    @staticmethod
    def _parse_result(out, key):
        for line in (out or '').splitlines():
            if line.startswith(key + '='):
                return line[len(key) + 1:].strip()
        return ''

    # ---- crons -----------------------------------------------------------

    @api.model
    def _cron_nightly_backups(self):
        """Nightly: one backup per ready tenant, typed for the retention pyramid
        (monthly on the 1st, weekly on Sunday, else daily)."""
        btype = self._backup_type_for_today()
        tenants = self.env['ncollection.tenant'].search([
            ('database_status', '=', 'ready'), ('database_name', '!=', False)])
        for tenant in tenants:
            self.create({'tenant_id': tenant.id, 'backup_type': btype})._enqueue()
        return True

    @staticmethod
    def _backup_type_for_today():
        today = fields.Date.today()
        if today.day == 1:
            return 'monthly'
        if today.weekday() == 6:  # Sunday
            return 'weekly'
        return 'daily'

    @api.model
    def _cron_prune(self):
        """Enforce 7 daily / 4 weekly / 12 monthly per tenant; delete the file
        and the record for anything beyond the window."""
        # Distinct tenants that HAVE backups (read_group avoids scanning every
        # record with a bare search([])).
        groups = self.read_group(
            [('status', '=', 'done')], ['tenant_id'], ['tenant_id'])
        tenant_ids = [g['tenant_id'][0] for g in groups if g['tenant_id']]
        for tenant_id in tenant_ids:
            for btype, keep in _RETENTION.items():
                recs = self.search([
                    ('tenant_id', '=', tenant_id), ('backup_type', '=', btype),
                    ('status', '=', 'done')], order='create_date desc')
                for rec in recs[keep:]:
                    rec._delete_file()
                    rec.unlink()
        return True

    def _delete_file(self):
        self.ensure_one()
        if self.file_path and os.path.isfile(self.file_path):
            try:
                os.remove(self.file_path)
            except OSError as exc:
                _logger.warning("Could not delete backup file %s: %s", self.file_path, exc)

    @api.model
    def _cron_restore_drill(self):
        """§5.3 monthly drill: restore the newest good backup to a scratch DB and
        confirm it is non-empty. Proves the backups are restorable, not just
        present. Logged on the backup record."""
        rec = self.search([('status', '=', 'done'), ('file_path', '!=', False)],
                          limit=1, order='create_date desc')
        if not rec:
            return False
        target = 'drill_%s' % (rec.database_name or 'tenant')
        # Safety (P3-T12 / ISO-2): the unattended drill dropdb+createdb's `target`.
        # Never let it clobber a LIVE tenant DB — mirror the interactive wizard's
        # guard (BackupRestoreWizard.action_restore). If a tenant somehow owns
        # this name, skip + alert rather than destroy a real tenant's database.
        if self.env['ncollection.tenant'].sudo().with_context(
                active_test=False).search_count([('database_name', '=', target)]):
            rec.message_post(body=self.env._(
                "Restore drill SKIPPED: scratch name '%s' collides with a live "
                "tenant database — refusing to overwrite it.", target))
            rec._alert_failure()
            return False
        # Whether `target` existed BEFORE we touched anything decides what the
        # cleanup is allowed to assume (#287). restore_to can raise before
        # tenant_restore.sh ever reaches its dropdb/createdb -- a missing
        # file_path, either ownership guard, or a timeout during decrypt -- and
        # in that case this run created nothing.
        pre_existing = rec._drill_database_exists(target)
        failure = None
        try:
            rec.restore_to(target)
        except Exception as exc:  # pylint: disable=broad-except
            failure = exc
        finally:
            dropped = rec._drop_drill_database(
                target, pre_existing=pre_existing)

        # Report AFTER the cleanup, never before. The first version posted
        # "then dropped" inside the try, so a drop that later failed left the
        # chatter -- the audit trail an operator actually reads -- asserting
        # the opposite of what happened, with only a log line to contradict it.
        # A leftover reachable copy of tenant data is precisely what this
        # ticket exists to surface; the message must not hide it.
        scrap = (self.env._("scratch copy removed") if dropped
                 else self.env._(
                     "SCRATCH COPY NOT REMOVED -- '%s' still exists and holds "
                     "tenant data; drop it by hand", target))
        if failure is None:
            rec.message_post(body=self.env._(
                "Restore drill OK: %(file)s restored to scratch DB %(db)s "
                "(%(scrap)s).", file=rec.file_path, db=target, scrap=scrap))
        else:
            rec.message_post(body=self.env._(
                "Restore drill FAILED: %(err)s (%(scrap)s).",
                err=failure, scrap=scrap))
            rec._alert_failure()
        return True

    @api.model
    def _tenant_backup_dir(self, db):
        """The directory tenant_backup.sh writes this tenant's dumps into."""
        return os.path.join(self._backup_root(), db)

    @api.model
    def _tenant_backup_dir_exists(self, db):
        """Does this name already carry backup files? Never raises -- name
        generation must not fail because the filesystem is unreadable, and a
        probe that cannot answer must not block provisioning."""
        try:
            return os.path.isdir(self._tenant_backup_dir(db))
        except Exception:  # pylint: disable=broad-except
            _logger.warning(
                "Could not check for leftover backups under '%s'.", db)
            return False

    @api.model
    def _purge_tenant_backup_dir(self, db):
        """Delete a tenant's backup directory. Returns True when it is gone.

        Called when a tenant is really deleted (#295). `tenant_id` is
        ondelete='cascade', so removing a tenant already removes its backup
        ROWS -- but nothing removed the FILES, and they sit under a directory
        named after the tenant. `database_name` is reusable: it unlocks in
        `not_provisioned`/`error` (deliberately -- see ncollection.tenant.write,
        that is the generate/heal path) and `unique()` only constrains a point
        in time. So a freed name handed to a NEW tenant came with the previous
        tenant's dumps already sitting in "its" directory, and #288's ownership
        check -- which keys on the directory name -- would have accepted them.

        rmtree is irreversible, so the name is validated and the resolved path
        is asserted to sit INSIDE the backup root before anything is removed.
        A traversal or absolute-ish name must refuse, never delete.
        """
        subproc = self.env['ncollection.saas.subprocess.mixin']
        # The STRICT tenant rule, matching _assert_file_belongs_to_tenant --
        # this is a tenant's own database_name, not a scratch target. The two
        # sat on different rules for the same kind of value, which is how a
        # reader ends up trusting the wrong one. Strict is also the
        # conservative choice on an irreversible delete: a non-conforming name
        # (possible on an unlocked not_provisioned/error tenant) is REFUSED
        # and logged rather than deleted, and _tenant_backup_dir_exists still
        # blocks that name from being handed out again.
        subproc._assert_safe_db_name(db)
        root = os.path.realpath(self._backup_root())
        target = os.path.realpath(self._tenant_backup_dir(db))
        if target == root or not target.startswith(root + os.sep):
            raise ValidationError(self.env._(
                "Refusing to purge '%(t)s': it resolves outside the backup "
                "root %(r)s. rmtree is irreversible, so an unattributable "
                "path is never removed.", t=target, r=root))
        if not os.path.isdir(target):
            return True
        try:
            shutil.rmtree(target)
            _logger.info("Purged backup directory %s for deleted tenant '%s'.",
                         target, db)
            return True
        except OSError:
            # Loud, not fatal: leaving files behind is the very condition this
            # exists to prevent, and a caller mid-unlink cannot fix it.
            _logger.error(
                "Could not purge backup directory %s for deleted tenant '%s'. "
                "Its dumps remain on disk and the name may later be reused; "
                "remove it by hand.", target, db, exc_info=True)
            return False

    def _drill_database_exists(self, db):
        """Did `db` exist before this drill started? Never raises -- an
        unanswerable probe must not stop the drill, and the caller treats
        'unknown' as 'assume we created it', which is the safe default: it
        drops, and dropping a drill_* database is never wrong for data safety.
        """
        try:
            return self.env['ncollection.provisioning.job'].sudo(
                )._database_exists(db)
        except Exception:  # pylint: disable=broad-except
            _logger.warning(
                "Restore drill: could not check whether '%s' pre-existed.", db)
            return False

    def _drop_drill_database(self, target, pre_existing=False):
        """Delete the drill's scratch copy once it has served its purpose (#287).

        The drill exists to prove a backup RESTORES, and tenant_restore.sh
        already decides that by counting tables in the public schema and
        failing on zero. So the moment restore_to returns, the database has
        answered the only question it was created to answer -- and what is
        left behind is a full, unmanaged copy of one tenant's data.

        It was never dropped. Three properties made that worse than a stray
        database: the name is `drill_<the tenant's own public subdomain>`, so
        it is guessable; the drill re-runs monthly, so it persists; and it is
        REACHABLE -- db_filter matches the request Host against existing
        database names with no charset validation, and nginx forwards an
        underscored Host verbatim. Measured: a database that exists answers
        differently from one that does not (500 vs 303), i.e. Odoo really does
        route into it. Anyone reaching it meets a real login page whose working
        credentials are that tenant's password hashes AT SNAPSHOT TIME --
        including passwords rotated away from after an incident.

        In `finally` deliberately: a FAILED drill can still leave a partially
        restored database behind, and that copy is no less sensitive than a
        successful one. Dropping it costs diagnostic material, which is a
        conscious trade -- the schema check already failed loudly and the error
        is on the record, so the leftover database adds little that the chatter
        message does not.

        Returns True when the database is gone, False when it survived --
        the caller reports that in chatter, because "we dropped it" must never
        be asserted before it is known.

        _assert_scratch_db_name, NOT _assert_safe_db_name: `_drop_database`
        requires the name be validated first, and the strict tenant rule
        forbids the '_' that every drill target contains. Naming the wrong
        validator here would either reject every drop or skip the check.
        """
        subproc = self.env['ncollection.saas.subprocess.mixin']
        try:
            subproc._assert_scratch_db_name(target)
            if pre_existing:
                # Not ours: it was already there when this run began, so this
                # run may have created nothing (restore_to can raise before
                # the script's createdb). Dropping it anyway is still the
                # right call for DATA SAFETY -- a drill_* database holds a
                # tenant copy by definition and must not persist -- but say so
                # at INFO, because the one realistic owner is an operator who
                # restored a snapshot there by hand to investigate something.
                _logger.info(
                    "Restore drill: scratch database '%s' already existed "
                    "before this run and is being reclaimed. If that was a "
                    "manual investigation copy, use a name outside the "
                    "drill_* namespace -- this cron owns it.", target)
            subproc._drop_database(target)
            return True
        except Exception:  # pylint: disable=broad-except
            # Never let cleanup mask the drill's own verdict. A drop that fails
            # is worth an ERROR line (P2-T10 watches those) but the restore
            # result -- success or the real failure -- is what the operator
            # came for and must survive this block.
            _logger.error(
                "Restore drill: could not drop scratch database '%s'. It now "
                "holds a copy of tenant data and is reachable by Host header; "
                "drop it by hand.", target, exc_info=True)
            return False

    # ---- alerting --------------------------------------------------------

    def _alert_failure(self):
        self.ensure_one()
        body = self.env._(
            "Tenant backup FAILED for %(db)s (%(type)s).",
            db=self.database_name, type=self.backup_type)
        self.message_post(body=body)
        self.activity_schedule(
            'mail.mail_activity_data_todo',
            summary=self.env._("Investigate backup failure: %s", self.database_name),
            note=body)
