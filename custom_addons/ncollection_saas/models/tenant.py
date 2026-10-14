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

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

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

        Deferred to POSTCOMMIT, not run inline. rmtree has no undo, while a
        DELETE does: Cursor.commit() runs postcommit.run() only after the SQL
        commit succeeds, and rollback() calls postcommit.clear(). Purging
        inline meant that if anything later in the SAME transaction raised --
        another step in the same request, a queue_job that retries, a mail
        side effect -- the tenant and its backup ROWS came back while the
        FILES were already gone, leaving live records pointing at nothing.
        Deferring makes the irreversible half wait for the reversible half to
        become durable.

        Purging never blocks the delete -- a filesystem that will not
        cooperate must not strand a tenant record -- but it logs ERROR, the
        signal P2-T10 watches.
        """
        Backup = self.env['ncollection.backup'].sudo()
        names = [t.database_name for t in self if t.database_name]
        res = super().unlink()
        for db in names:
            self.env.cr.postcommit.add(
                lambda db=db: self._purge_after_commit(Backup, db))
        return res

    @api.model
    def _purge_after_commit(self, Backup, db):
        """Postcommit callback: the delete is durable, now remove the files.

        Broad except on purpose -- this runs OUTSIDE the request's error
        handling, where an escaping exception would surface as an unrelated
        failure long after the delete succeeded.
        """
        if db in _GENERATED_NAME_BLOCKLIST:
            # Another suite's namespace. tenant.py documents that the fixture
            # suites legitimately create REAL tenant rows under these names,
            # and CLAUDE.md's ownership table says each suite may drop only
            # its own. Nothing else ties "who is deleting" to "whose files",
            # and this delete is irreversible -- so a record squatting on a
            # reserved name must never take that namespace's dumps with it.
            # The suites clean up via their own `dropdb`, never through here.
            _logger.warning(
                "Refusing to purge backups for '%s': that name belongs to a "
                "reserved/fixture namespace, which this delete does not own.",
                db)
            return
        try:
            Backup._purge_tenant_backup_dir(db)
        except Exception:  # pylint: disable=broad-except
            _logger.error(
                "Tenant '%s' was deleted but its backup directory could not "
                "be purged; the name must not be reused until it is.",
                db, exc_info=True)

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
            if not _GENERATED_NAME_RE.fullmatch(name):
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
        # THE SUBDOMAIN IS THE DATABASE IDENTITY. Under db_filter=^%d$ a request
        # for <label>.<base-domain> reaches the database named <label> and no
        # other, so a tenant whose database_name differs from its subdomain has
        # a URL that routes nowhere. When the operator (or the customer) chose a
        # valid subdomain it therefore wins over a name generated from the
        # company; the generator only runs when no subdomain was chosen.
        subdomain = (self.subdomain or '').strip().lower()
        if subdomain and _GENERATED_NAME_RE.fullmatch(subdomain):
            if self.database_name != subdomain:
                self.database_name = subdomain
            return subdomain
        current = self.database_name
        if current and _GENERATED_NAME_RE.fullmatch(current):
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

    # ------------------------------------------------------------------
    # Approval + ONE-CLICK provisioning
    # ------------------------------------------------------------------
    # The administrator's whole job is one button. Every state it can meet maps
    # to one outcome, and none of them needs a terminal, SQL or the Odoo shell:
    #
    #   pending   -> approve, then provision          ("Approve & Provision")
    #   rejected  -> refused: a rejection is a decision, not a retryable fault
    #   approved  -> create the subscription if absent, then provision
    #   failed    -> re-run the SAME failed job (see _trigger_provisioning)
    #   running / ready -> nothing to do; say so rather than build a second one
    #
    # It calls the EXISTING engine (subscription -> _trigger_provisioning ->
    # job.action_run). There is no second provisioning implementation here.

    def _nc_check_platform_admin(self):
        """Rule 4: the menu hides these buttons, but the ORM is the control."""
        if not (self.env.su or self.env.user.has_group(
                'ncollection_subscription.group_platform_admin')):
            raise UserError(self.env._(
                "Only a platform administrator can approve or provision a tenant."))

    def action_approve(self):
        self._nc_check_platform_admin()
        for tenant in self:
            if tenant.registration_state == 'approved':
                continue
            tenant.write({'registration_state': 'approved'})
            tenant.message_post(body=self.env._(
                "Registration approved by %s.", self.env.user.name))
        return True

    def action_reject(self):
        self._nc_check_platform_admin()
        for tenant in self:
            tenant._nc_release_registration(
                self.env._("Registration rejected by %(user)s.", user=self.env.user.name))
        return True

    @api.model
    def _nc_repair_legacy_portal_urls(self):
        """Rewrite the stored ``portal_url`` of tenants that still carry the RETIRED dev template.

        ``portal_url`` is a snapshot taken at provisioning. Until the one URL builder existed it was filled from a
        template whose built-in value was ``http://<db>.localhost:8069``, so a tenant provisioned then keeps that
        address next to its (correct) ``tenant_url``. Only that exact legacy value is replaced, and only by what the
        builder says (nothing is rewritten when no base domain is configured). Any other value, including one an
        operator typed, is left alone. Idempotent.
        """
        fixed = self.browse()
        for tenant in self.sudo().search([('portal_url', '=like', 'http://%.localhost:8069')]):
            label = tenant.database_name
            if not label or tenant.portal_url != 'http://%s.localhost:8069' % label:
                continue
            origin = tenant._nc_public_origin()
            if origin and origin != tenant.portal_url:
                tenant.portal_url = origin
                fixed |= tenant
        return fixed

    def _nc_release_registration(self, message):
        """Reject ONE request and release its subdomain. The single path for
        both an administrator's Reject and the automatic expiry, so the two can
        never disagree about what rejecting means."""
        self.ensure_one()
        if self.database_status in ('provisioning', 'ready'):
            raise UserError(self.env._(
                "'%s' already has a workspace; it cannot be rejected.",
                self.company_name))
        requested = self.subdomain or self.database_name
        note = self.registration_decision_note or ''
        # Release the subdomain: a rejected request must not hold the name
        # hostage (database_name is unique, and the availability probe would
        # report it 'taken' forever). What they asked for is kept in the
        # decision note so the record still tells the whole story.
        self.write({
            'registration_state': 'rejected',
            'subdomain': False,
            'database_name': False,
            'admin_password_hash': False,
            'registration_decision_note': ('%s [requested subdomain: %s]' % (
                note, requested)).strip() if requested else note,
        })
        self.message_post(body='%s %s' % (message, note))

    @api.model
    def _cron_expire_pending_registrations(self):
        """Release subdomains held by requests nobody decided on.

        A pending request reserves its subdomain, and nothing else ever lets go
        of it, so an abandoned sign-up would hold the name forever. After
        `ncollection_saas.pending_registration_days` (default 30; 0 disables) the
        request is rejected through the SAME path an administrator's Reject uses:
        nothing is deleted, no database was ever created, the record keeps the
        subdomain it asked for in its decision note.
        """
        days = int(self.env['ir.config_parameter'].sudo().get_param(
            'ncollection_saas.pending_registration_days', '30') or 0)
        if days <= 0:
            return 0
        cutoff = fields.Datetime.subtract(fields.Datetime.now(), days=days)
        stale = self.search([
            ('registration_state', '=', 'pending'),
            ('database_status', '=', 'not_provisioned'),
            ('registration_date', '<', cutoff),
        ])
        for tenant in stale:
            tenant._nc_release_registration(self.env._(
                "Registration expired: no decision within %(days)s days.", days=days))
        return len(stale)

    def action_provision(self):
        """Approve (if pending) and create the tenant's workspace — one click."""
        self._nc_check_platform_admin()
        messages = []
        for tenant in self:
            messages.append(tenant._nc_provision_one())
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': self.env._("Tenant provisioning"),
                'message': ' '.join(messages),
                'type': 'info',
                'sticky': False,
                # The button changed the record (approval, stage, buttons), and a
                # client action leaves the open form as it was — so a customer
                # would still see "Pending Approval" and an "Approve" button
                # after approving. Reload the record's data, keep the page.
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    def _nc_provision_one(self):
        self.ensure_one()
        name = self.company_name
        if self.registration_state == 'rejected':
            raise UserError(self.env._(
                "'%s' was rejected. Reopen the request before provisioning.", name))
        if self.database_status == 'ready':
            return self.env._("'%s' is already provisioned; nothing was created.", name)
        if self.database_status == 'provisioning':
            return self.env._("'%s' is already being provisioned.", name)
        if not self.plan_id:
            raise UserError(self.env._(
                "Choose a subscription plan for '%s' before provisioning.", name))
        # Fail on a bad username HERE, with the form still open, rather than
        # later in a queue job nobody is watching.
        self._nc_require_admin_login()

        if self.registration_state == 'pending':
            self.action_approve()

        sub = self.subscription_id or self.subscription_ids[:1]
        if not sub:
            sub = self.env['ncollection.subscription'].sudo().create({
                'tenant_id': self.id,
                'plan_id': self.plan_id.id,
                'status': 'draft',
                'start_date': fields.Date.context_today(self),
            })
            self.sudo().subscription_id = sub.id
        retry = self.database_status == 'error'
        if sub.status == 'draft':
            # The same default the public registration uses — one definition.
            from odoo.addons.ncollection_saas.controllers.checkout import TRIAL_DAYS
            trial_end = fields.Date.add(
                fields.Date.context_today(self),
                days=self.plan_id.trial_days or TRIAL_DAYS)
            # draft -> trial starts the lifecycle AND triggers provisioning
            # (see subscription.action_start_trial), exactly once.
            sub.action_start_trial()
            # action_start_trial stamps trial_end_date = today + plan.trial_days,
            # and every seeded plan has trial_days = 0 — which the daily sweep
            # reads as "this trial ends today", expiring a customer the moment
            # they are approved. So the trial clock is set explicitly, and it
            # starts NOW (at approval), not when the request was filed: a request
            # that waited three days for a decision must not lose three days of
            # the trial it was promised.
            sub.trial_end_date = trial_end
            self.sudo().trial_end_date = trial_end
        else:
            sub._trigger_provisioning()
        if retry:
            return self.env._(
                "Retrying provisioning for '%s'. The previous attempt's job is "
                "re-run; no second database or user is created.", name)
        return self.env._(
            "Provisioning started for '%s'. The workspace will be ready in a "
            "few minutes.", name)
