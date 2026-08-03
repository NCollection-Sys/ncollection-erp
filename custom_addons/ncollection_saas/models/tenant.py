# -*- coding: utf-8 -*-
"""Tenant SaaS-layer behaviour (P2-T02).

The base ncollection.tenant model (fields, lifecycle) lives in
ncollection_subscription. Here we add the provisioning-pipeline concerns that
belong with the SaaS runner: deriving a routable database name from the company
name, the welcome email on success, and the admin alert on failure. Kept in
ncollection_saas because only this layer depends on queue_job / the engine.
"""

import logging
import re
import unicodedata

from odoo import api, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

# database_status states in which the tenant's Postgres DB exists (or is being
# built), so its name is fixed. The name IS the DB + subdomain identity
# (tenant key === subdomain === database name under db_filter=^%d$).
_NAME_LOCKED_STATES = ('provisioning', 'ready')

# Names a tenant DB must never take — in two tiers:
#
# _RESERVED_PLATFORM_DB_NAMES — genuinely NON-tenant databases: the Postgres
#   maintenance DBs, the platform control-plane / demo DBs, and reserved vanity
#   subdomains. A tenant is HARD-blocked (ISO-1) from claiming one once it is
#   provisioning/ready — a tenant named after the control DB could drive
#   config-sync at the control plane. The live platform DB (env.cr.dbname) is
#   added at runtime. These are never legitimate tenant workspaces.
#
# _GENERATED_NAME_BLOCKLIST — the reserved-platform set PLUS the per-suite
#   tenant-fixture namespaces (routing/e2e/provisioning, whose *-clean targets
#   could later drop a same-named real tenant) + the demo/CI tenants. ONLY the
#   auto-generator avoids these, so a real customer's slug never lands on a
#   fixture name. It is NOT a hard constraint: those suites legitimately CREATE
#   tenants with these exact names (provclient, rtclienta, albarari …), so
#   hard-blocking them here would break the very suites that own them.
#
# Generated names are ALSO strictly alphanumeric (no underscore) so they are
# valid subdomains — tenant key === subdomain === database name.
_RESERVED_PLATFORM_DB_NAMES = frozenset({
    'admin', 'www', 'staging', 'api',
    'postgres', 'template0', 'template1',
    'ncollection', 'ncollectiondemo', 'ncplatform',
})
_GENERATED_NAME_BLOCKLIST = _RESERVED_PLATFORM_DB_NAMES | frozenset({
    'albarari', 'saastest', 'citest',
    'rtclienta', 'rtclientb', 'rtadmin',
    'e2eclienta', 'e2eclientb', 'e2eadmin',
    'provclient', 'provfail',
})
_GENERATED_NAME_RE = re.compile(r'^[a-z][a-z0-9]{2,62}$')


class Tenant(models.Model):
    _inherit = 'ncollection.tenant'

    def write(self, vals):
        """Enforce database_name immutability once the DB exists — the ORM mirror
        of the form's read-only rule (Rule 4: a UI restriction is never the
        substance of authorization). Blocked while provisioning or ready; still
        free when not_provisioned or after an error (the generate/heal path). The
        name IS the live database + subdomain, so changing it here would orphan the
        real database and break db_filter routing.

        ISO-1 (#225): also block when the SAME write flips status INTO a locked
        state. The old guard read only the pre-write status, so a combined
        write({'database_name': <victim>, 'database_status': 'ready'}) on a
        not_provisioned record slipped through — the lever behind the cross-tenant
        config-sync takeover. Evaluate the effective (post-write) status too."""
        if 'database_name' in vals:
            new_status = vals.get('database_status')
            for tenant in self:
                locked = (tenant.database_status in _NAME_LOCKED_STATES
                          or new_status in _NAME_LOCKED_STATES)
                if locked and vals['database_name'] != tenant.database_name:
                    raise ValidationError(self.env._(
                        "The database name of a provisioned tenant is immutable — it "
                        "is the live database and subdomain (tenant %s). Changing it "
                        "would orphan the database and break routing.",
                        tenant.database_name or tenant.company_name))
        return super().write(vals)

    def unlink(self):
        """Take the tenant's backup FILES with it (#295).

        `ncollection.backup.tenant_id` is ondelete='cascade', so deleting a
        tenant already removes its backup ROWS -- but the dumps stayed on disk,
        under a directory named after `database_name`. That name is REUSABLE:
        it unlocks in not_provisioned/error (deliberately, see write() above --
        the generate/heal path) and `unique()` only holds at a point in time.
        A freed name given to a NEW tenant therefore arrived with the previous
        tenant's dumps already in "its" directory, and #288's ownership check
        keys on exactly that directory name.

        Collected BEFORE super(): once the rows are gone the names are too.
        Purging never blocks the delete -- a filesystem that will not cooperate
        must not strand a tenant record -- but it logs ERROR, which is the
        signal P2-T10 watches.
        """
        Backup = self.env['ncollection.backup'].sudo()
        names = [t.database_name for t in self if t.database_name]
        res = super().unlink()
        for db in names:
            try:
                Backup._purge_tenant_backup_dir(db)
            except Exception:  # pylint: disable=broad-except
                _logger.error(
                    "Tenant '%s' was deleted but its backup directory could "
                    "not be purged; the name must not be reused until it is.",
                    db, exc_info=True)
        return res

    @api.constrains('database_name', 'database_status')
    def _check_locked_database_name(self):
        """ISO-1 (#225): once a tenant is provisioning/ready its database_name IS
        the live DB + subdomain (db_filter=^%d$), so enforce the FULL name policy
        at the model layer — not just the checkout controller / provisioning
        runtime. Fires on create AND write (a status-only flip to 'ready' or a
        privileged create at 'ready' both reach here), closing the gaps where a
        tenant could otherwise become routable with an unroutable/reserved name.
        not_provisioned tenants stay exempt: the _ensure_database_name heal path
        legitimately holds a raw name transiently, and a name on a DB-less tenant
        is harmless. (Uniqueness itself is the DB-level UNIQUE constraint.)"""
        reserved = _RESERVED_PLATFORM_DB_NAMES | {self.env.cr.dbname}
        for tenant in self:
            if tenant.database_status not in _NAME_LOCKED_STATES:
                continue
            name = tenant.database_name
            if not name:
                continue
            if not _GENERATED_NAME_RE.match(name):
                raise ValidationError(self.env._(
                    "Invalid database name '%s' — it must be lowercase alphanumeric, "
                    "start with a letter, and be 3–63 characters (no underscores, "
                    "hyphens or path characters): it is the database name and the "
                    "tenant subdomain.", name))
            if name in reserved:
                raise ValidationError(self.env._(
                    "Database name '%s' is reserved (a platform or maintenance "
                    "database) and cannot be assigned to a tenant.", name))

    # ---- database-name generation (P2-T02 point 1) -----------------------

    def _ensure_database_name(self):
        """Give the tenant a valid, routable, collision-free DB name if it has
        none (or an unroutable one). Idempotent: a name that already validates
        is kept."""
        self.ensure_one()
        current = self.database_name
        if current and _GENERATED_NAME_RE.match(current):
            return current
        name = self._generate_database_name(self.company_name)
        self.database_name = name
        return name

    def _generate_database_name(self, company_name):
        """Slugify a company name to ^[a-z][a-z0-9]{2,62}$, then de-collide.

        Collision space = the live cluster (pg_database) + the reserved/fixture
        blocklist + the platform DB — a numeric suffix is appended until free.
        """
        base = self._slugify_db_name(company_name)
        blocked = _GENERATED_NAME_BLOCKLIST | {self.env.cr.dbname}
        Job = self.env['ncollection.provisioning.job']
        Backup = self.env['ncollection.backup'].sudo()
        candidate = base
        suffix = 1
        # Leftover BACKUP FILES are a third way a name is taken (#295).
        # The live cluster and the blocklist both miss it: a previous tenant's
        # dumps can outlive the tenant AND its database, and handing the name
        # to someone new would seat them on top of another tenant's data --
        # which #288's directory-keyed ownership check would then accept.
        # _purge_tenant_backup_dir removes them on delete; this covers what is
        # already on disk, and any purge that failed loudly.
        while (candidate in blocked
               or Job._database_exists(candidate)
               or Backup._tenant_backup_dir_exists(candidate)):
            suffix += 1
            tail = str(suffix)
            candidate = '%s%s' % (base[:63 - len(tail)], tail)
        return candidate

    @staticmethod
    def _slugify_db_name(name):
        """ASCII-fold, strip to alphanumeric, guarantee a letter start and the
        3–63 length window."""
        folded = unicodedata.normalize('NFKD', name or '').encode('ascii', 'ignore').decode()
        slug = re.sub(r'[^a-z0-9]+', '', folded.lower())
        if not slug or not slug[0].isalpha():
            slug = 'nc' + slug
        slug = slug[:63]
        if len(slug) < 3:
            slug = (slug + 'tenant')[:63]
        return slug

    # ---- notifications (P2-T02 points 3 & 4) -----------------------------

    def _send_welcome_email(self, setup_url=None):
        """Queue the branded welcome email (never fail provisioning on mail
        transport — the user_invite.py precedent). Dev has no SMTP, so this
        produces a queued mail.mail row rather than a delivery."""
        self.ensure_one()
        template = self.env.ref(
            'ncollection_saas.mail_template_tenant_welcome', raise_if_not_found=False)
        if not template or not self.email:
            return
        try:
            template.with_context(nc_setup_url=setup_url or '').send_mail(
                self.id,
                email_layout_xmlid='mail.mail_notification_light',
                force_send=False,
            )
        except Exception:  # noqa: BLE001 - transport must never break the flow
            _logger.warning(
                "Welcome email could not be queued for tenant %s (no mail server?)",
                self.id, exc_info=True)

    def _notify_provisioning_failure(self, log):
        """Alert the platform admins that provisioning failed, with the job log.

        Posts to the tenant chatter (durable, visible on the record) and best-
        effort emails the system-admin group. Transport failures are swallowed."""
        self.ensure_one()
        excerpt = (log or '')[-1500:]
        body = self.env._(
            "Provisioning FAILED for tenant %(name)s (database '%(db)s'). "
            "Latest job log:\n%(log)s",
            name=self.company_name, db=self.database_name or '-', log=excerpt)
        try:
            self.message_post(body=body.replace('\n', '<br/>'))
            admins = self.env.ref('base.group_system').all_user_ids
            partners = admins.partner_id
            if partners:
                self.message_notify(
                    partner_ids=partners.ids,
                    subject=self.env._("Tenant provisioning failed: %s", self.company_name),
                    body=body.replace('\n', '<br/>'),
                )
        except Exception:  # noqa: BLE001 - alerting must never mask the failure
            _logger.warning("Could not post provisioning-failure alert for tenant %s",
                            self.id, exc_info=True)
