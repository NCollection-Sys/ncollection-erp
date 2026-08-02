# -*- coding: utf-8 -*-
"""ncollection.auth.log — the tenant-local authentication audit trail (P1-T19).

Design rules (ARCHITECTURE_SECURITY.md §6):
- Logging ONLY. This model never participates in authentication decisions —
  enforcement is Odoo core's login cooldown + OCA auth_session_timeout.
- Failure events are written through a SEPARATE cursor (`_capture_isolated`)
  because the failed-login exception rolls the request transaction back —
  a same-cursor row would vanish with it.
- Rows are readable by Settings/system users only and writable by no one
  (hooks write via sudo); mirrored in ir.model.access.csv (Rule 4).
"""

import hashlib
import logging
import secrets
from datetime import timedelta

from odoo import SUPERUSER_ID, api, fields, models
from odoo.exceptions import UserError
from odoo.http import request

_logger = logging.getLogger(__name__)

# Row-level retention (#219). ARCHITECTURE_SECURITY documented deletion only at
# tenant OFFBOARDING (DB drop); this table needed a policy for a LIVE tenant,
# because ip_address + user_agent are PII written on every auth event and #178
# scaled that from one demo tenant to every provisioned one.
RETENTION_PARAM = 'ncollection_auth.log_retention_days'
DEFAULT_RETENTION_DAYS = 180

# Floor on any POSITIVE retention window. Before this purge existed, the ACL
# (ir.model.access.csv) granted base.group_system READ ONLY — perm_write,
# perm_create and perm_unlink are all 0, i.e. the table was append-only at the
# application layer and only OS/DB-shell access could remove a row. This cron is
# therefore the FIRST app-level deletion path into the auth audit trail, gated
# only by whoever can edit a system parameter.
#
# ir.config_parameter carries no audit trail of its own (write_uid/write_date
# are overwritten by the next write), so setting the window to 1, letting the
# purge run, and setting it back to 180 would erase the evidence of an intrusion
# and leave no record that the value ever changed — a textbook anti-forensics
# move against the very log meant to catch account compromise.
#
# A positive value below this floor is treated as a misconfiguration and REFUSED
# rather than clamped: clamping still deletes (just less), whereas refusing
# preserves the evidence under both the fat-finger and the malicious reading.
# Lowering this floor is a deliberate, reviewable code change.
MIN_RETENTION_DAYS = 30

# ---------------------------------------------------------------------------
#  Two-stage lifecycle (#261)
# ---------------------------------------------------------------------------
#  #219 shipped a single flat window: at RETENTION_PARAM days, hard-delete.
#  Its own security review flagged the tension that creates. Breach studies
#  (Verizon DBIR, IBM Cost of a Data Breach) put MEAN time-to-identify a
#  compromise past 200 days — so a 180-day delete destroys the login_failed
#  run-up AND the login_success for the compromised session before anyone
#  looks. That defeats the audit trail's stated purpose while satisfying
#  storage limitation: the wrong half of PDPL.
#
#  Raising the window instead would keep full IP + user-agent PII on every
#  ordinary login for a year, which is worse on storage limitation and does
#  nothing the minimised skeleton cannot do.
#
#  So: MINIMISE at RETENTION_PARAM, DELETE at SKELETON_PARAM.
#    stage 1 (180d) — drop ip_address and user_agent, digest `login`; keep
#                     event_type, create_date, user_id. The PATTERN survives,
#                     the identifying detail does not.
#    stage 2 (400d) — hard delete, as before.
#
#  The skeleton is PSEUDONYMOUS, NOT ANONYMOUS: user_id still points at a
#  person, so those rows remain personal data under PDPL and need their own
#  window. Anyone tempted to make stage 2 indefinite should read that twice —
#  "we anonymised it" would be a false premise for that decision.
SKELETON_PARAM = 'ncollection_auth.log_skeleton_days'
DEFAULT_SKELETON_DAYS = 400

# `login` is the raw string typed at the login form — an email address, and the
# most directly identifying field on the row. #261's option 3 said to null
# ip_address and user_agent and stopped there; leaving `login` would have made
# the minimisation cosmetic.
#
# It is DIGESTED rather than nulled because on the two most investigation-
# relevant event types it is the ONLY record of which account was involved:
# `_capture_isolated('login_failed', login=...)` and
# `_capture('reset_complete', login=...)` both pass no `user`, so those rows
# carry `login` with user_id NULL. Nulling it would erase the subject of a
# failed-login or completed-reset event entirely; digesting keeps "these 400
# failures all hit one account" without storing anyone's address.
#
# (An earlier version of this comment justified the digest by pointing at
# attempts against NON-EXISTENT users. That was wrong: res.users._login raises
# AccessDenied before _check_credentials when the user does not exist, so no row
# is written at all for that case — the hook never fires. Credential stuffing
# against invented addresses is invisible to this table either way, which is a
# real gap but a different one.)
#
# The prefix doubles as the already-minimised marker, so the sweep needs no new
# column and is naturally idempotent (Rule 12).
DIGEST_PREFIX = 'sha256:'
DIGEST_SALT_PARAM = 'ncollection_auth.log_digest_salt'

# Deleted oldest-first in chunks rather than one statement: on a tenant that has
# been logging for a long time the first run could otherwise hold a very large
# transaction. MAX_BATCHES bounds a single run so the cron cannot monopolise a
# worker — whatever is left is collected by the next daily run.
GC_CHUNK_SIZE = 5000
GC_MAX_BATCHES = 20

EVENT_TYPES = [
    ('login_success', 'Login success'),
    ('login_failed', 'Login failed'),
    ('logout', 'Logout'),
    ('reset_request', 'Password reset requested'),
    ('reset_complete', 'Password reset completed'),
]


class NcollectionAuthLog(models.Model):
    _name = 'ncollection.auth.log'
    _description = 'Authentication audit log'
    _order = 'create_date desc, id desc'

    event_type = fields.Selection(EVENT_TYPES, required=True, index=True)
    login = fields.Char(index=True)
    user_id = fields.Many2one('res.users', ondelete='set null', index=True)
    ip_address = fields.Char()
    user_agent = fields.Char()
    db_name = fields.Char()

    # The retention purge (#219) filters on create_date, which Odoo does NOT
    # index by default. Without this the nightly cron sequentially scans a table
    # that grows with every auth event on every tenant, forever. Declared via
    # models.Index because Odoo 19 ignores the legacy _sql_constraints list and
    # this is the same table-object API models.Constraint uses.
    _create_date_index = models.Index('(create_date)')

    # ------------------------------------------------------------------
    # Retention (#219)
    # ------------------------------------------------------------------

    @api.model
    def _retention_days(self):
        """Configured retention window in days. 0 or less means DISABLED.

        Reading through ir.config_parameter (rather than a module constant) is
        what makes the window tunable per tenant without a code change — the
        same pattern the module's other hardening knobs already use.

        Raises ``UserError`` on an unusable value rather than guessing. An
        earlier version fell back to the 180-day default on a parse failure,
        which is the wrong direction for a DESTRUCTIVE operation: an operator
        setting "3,650" for a ten-year mandate would have had the typo silently
        replaced by a much SHORTER window, and the job would then have deleted
        exactly the data they were trying to keep. Errors must not cause
        unintended state change. Raising also makes the run show as failed in
        Settings > Technical > Scheduled Actions instead of logging a line
        nobody tails.
        """
        raw = self.env['ir.config_parameter'].sudo().get_param(
            RETENTION_PARAM, DEFAULT_RETENTION_DAYS)
        # Logged AND raised, deliberately. Raising is what makes the run show as
        # failed in Scheduled Actions, but Odoo core deactivates a cron after 5
        # failures spanning 7 days (ir_cron._update_failure_count), so a
        # misconfiguration left unfixed eventually switches the purge OFF. That
        # is the lesser evil — refusing to delete beats deleting on a guessed
        # window — but it must not be invisible, so the log line gives
        # monitoring a second, independent channel that does not depend on
        # anyone watching the in-tenant Discuss notification.
        try:
            days = int(raw)
        except (TypeError, ValueError, OverflowError):
            _logger.error(
                "Auth-log retention misconfigured: %s = %r is not a whole "
                "number of days. No rows deleted; the purge will keep failing "
                "and Odoo will deactivate it after repeated failures.",
                RETENTION_PARAM, raw)
            raise UserError(self.env._(
                "Auth-log retention is misconfigured: %(param)s is %(value)r, "
                "which is not a whole number of days. No rows were deleted. "
                "Set it to a positive number of days (minimum %(minimum)s), "
                "or to 0 to disable the purge.",
                param=RETENTION_PARAM, value=raw, minimum=MIN_RETENTION_DAYS,
            )) from None

        if 0 < days < MIN_RETENTION_DAYS:
            raise UserError(self.env._(
                "Auth-log retention is set to %(days)s day(s), below the "
                "%(minimum)s-day minimum. No rows were deleted. This log is the "
                "audit trail for account compromise, so an unusually short "
                "window is refused rather than applied. Use 0 to disable the "
                "purge deliberately.",
                days=days, minimum=MIN_RETENTION_DAYS,
            ))
        return days

    @api.model
    def _skeleton_days(self):
        """Window after which a MINIMISED row is hard-deleted.

        Must be >= the minimise window: a skeleton window shorter than the
        retention window would delete rows before they were ever minimised,
        silently collapsing the two-stage policy back into #219's single flat
        delete — the exact behaviour #261 exists to replace. Refused, not
        clamped, for the same reason MIN_RETENTION_DAYS is refused: clamping
        still deletes, just differently than the operator believes.
        """
        raw = self.env['ir.config_parameter'].sudo().get_param(
            SKELETON_PARAM, DEFAULT_SKELETON_DAYS)
        try:
            days = int(str(raw).strip())
        except (TypeError, ValueError) as exc:
            # Logged AND raised, matching _retention_days: the log line is a
            # second channel that does not depend on anyone watching an
            # in-tenant notification.
            _logger.error(
                "Auth-log purge REFUSED: %s is not a whole number (%r).",
                SKELETON_PARAM, raw)
            raise UserError(self.env._(
                "%(param)s is not a whole number of days (%(value)r). Refusing "
                "to run: a purge that cannot read its own window must not guess.",
                param=SKELETON_PARAM, value=raw)) from exc

        # An INDEPENDENT floor, checked before the coherence rule and NOT
        # conditional on retention.
        #
        # The first version only compared skeleton against retention, which is
        # void whenever retention <= 0 — and retention 0 is a documented,
        # legitimate way to disable stage 1. That left `retention=0,
        # skeleton=1` accepted: a straight regression of the MIN_RETENTION_DAYS
        # anti-forensics floor this module documents at length, reachable in two
        # parameter writes instead of one, by exactly the actor that floor
        # exists to constrain. Deletion is the irreversible stage; it needs its
        # own floor, not a relative one.
        if 0 < days < MIN_RETENTION_DAYS:
            _logger.error(
                "Auth-log purge REFUSED: %s = %s is below the %s-day floor.",
                SKELETON_PARAM, days, MIN_RETENTION_DAYS)
            raise UserError(self.env._(
                "%(param)s is %(value)s day(s), below the %(minimum)s-day floor. "
                "Refusing: a window this short is how an audit trail gets erased "
                "and made to look like routine retention. Lowering the floor is a "
                "deliberate, reviewable code change.",
                param=SKELETON_PARAM, value=days, minimum=MIN_RETENTION_DAYS))

        retention = self._retention_days()
        if days > 0 and retention > 0:
            # A GAP, not just non-inversion. `skeleton == retention` passed the
            # original strict `<` test while meaning "minimise and delete on the
            # same day" — i.e. exactly the flat delete #261 exists to replace,
            # with every individual guard technically satisfied and no error. The
            # skeleton has to actually exist for long enough to be evidence.
            if days - retention < MIN_RETENTION_DAYS:
                _logger.error(
                    "Auth-log purge REFUSED: %s (%s) leaves only %s day(s) of "
                    "skeleton after %s (%s).",
                    SKELETON_PARAM, days, days - retention,
                    RETENTION_PARAM, retention)
                raise UserError(self.env._(
                    "%(sk)s (%(sv)s) leaves only %(gap)s day(s) of minimised "
                    "history after %(rt)s (%(rv)s) — less than the %(minimum)s-day "
                    "minimum. A skeleton that short is a flat delete wearing two "
                    "stages. Refusing.",
                    sk=SKELETON_PARAM, sv=days, gap=days - retention,
                    rt=RETENTION_PARAM, rv=retention, minimum=MIN_RETENTION_DAYS))
        elif days > 0 and retention <= 0:
            # The mirror-image misconfiguration, and it is silent: minimisation
            # off + deletion on means FULL raw ip/user-agent/login is kept right
            # up to the delete boundary — precisely the "keep a year of PII"
            # alternative this design rejected. Not refused (disabling stage 1 is
            # a documented, legitimate choice) but it must not pass unremarked.
            _logger.warning(
                "Auth-log: minimisation is DISABLED (%s = %s) while the purge "
                "keeps rows for %s days — full IP/user-agent/login are retained "
                "for that whole window.", RETENTION_PARAM, retention, days)
        return days

    @api.model
    def _login_digest_salt(self):
        """Per-tenant salt, generated once and kept.

        Without a salt the digest of a common address is trivially recovered
        from a precomputed table — the whole point is that the minimised row
        stops naming a person. It does NOT defend against someone who already
        holds DB access; it defends against the digest being casually
        reversible.
        """
        params = self.env['ir.config_parameter'].sudo()
        salt = params.get_param(DIGEST_SALT_PARAM)
        if not salt:
            salt = secrets.token_urlsafe(32)
            params.set_param(DIGEST_SALT_PARAM, salt)
        return salt

    @api.model
    def _login_digest(self, login):
        """Stable, salted digest of a login. Same input -> same output within a
        tenant, so 'repeatedly targeted' survives; across tenants it does not,
        which is correct — these are separate databases."""
        digest = hashlib.sha256(
            ('%s|%s' % (self._login_digest_salt(), login)).encode()).hexdigest()
        return '%s%s' % (DIGEST_PREFIX, digest)

    def _minimise_auth_log(self):
        """Stage 1: strip identifying detail from rows past the retention window.

        Keeps event_type, create_date and user_id — enough to answer "when, how
        often, which account" long after the IP and user-agent are gone.

        Idempotent by construction (Rule 12): the domain selects only rows that
        still HAVE something to strip, so a second run is a genuine no-op rather
        than a rewrite. That is also why no `minimised` column is needed — the
        DIGEST_PREFIX on `login` is the marker.
        """
        days = self._retention_days()
        if days <= 0:
            _logger.info(
                "Auth-log minimisation is disabled (%s = %s).",
                RETENTION_PARAM, days)
            return 0

        deadline = fields.Datetime.now() - timedelta(days=days)
        under_cron = bool(self.env.context.get('cron_id'))
        total = 0
        capped = True
        for _batch in range(GC_MAX_BATCHES):
            rows = self.sudo().search([
                ('create_date', '<', deadline),
                '|', '|',
                ('ip_address', '!=', False),
                ('user_agent', '!=', False),
                '&', ('login', '!=', False),
                # `not =like`, NOT `not like`: Odoo wraps a `like` value in
                # %...% (need_wildcard = '=' not in operator, orm/fields.py),
                # which made this "does not CONTAIN sha256:" — a substring test,
                # while the marker is a prefix. Idempotency survived either way,
                # but a login containing 'sha256:' anywhere would have been
                # skipped forever.
                ('login', 'not =like', DIGEST_PREFIX + '%'),
            ], limit=GC_CHUNK_SIZE, order='create_date asc')
            if not rows:
                capped = False
                break
            for row in rows:
                vals = {'ip_address': False, 'user_agent': False}
                if row.login and not row.login.startswith(DIGEST_PREFIX):
                    vals['login'] = self._login_digest(row.login)
                row.write(vals)
            total += len(rows)
            if under_cron:
                self.env['ir.cron']._commit_progress(processed=len(rows))
            if len(rows) < GC_CHUNK_SIZE:
                capped = False
                break

        if total:
            _logger.info("Auth-log minimised %s row(s) older than %s days.",
                         total, days)
        if capped:
            # Mirrors _gc_auth_log's warning, for the same reason: without it a
            # multi-day backlog looks identical to a clean sweep, while rows
            # keep their full PII past the window they were promised.
            _logger.warning(
                "Auth-log minimisation hit its per-run cap (%s rows); a backlog "
                "remains and will be picked up by the next run.", total)
        return total

    def _gc_auth_log(self):
        """Delete auth-log rows past the retention window. Returns the count.

        Called by the ``ir.cron`` in ``data/auth_cron.xml``. Deliberately a
        named scheduled action rather than an ``@api.autovacuum`` hook: under
        UAE PDPL this purge is evidence, and a distinct Scheduled Action has
        its own last-run/next-run timestamp that an auditor can point at and an
        operator can disable on its own. An autovacuum hook is bundled
        anonymously into Odoo's shared "Base: Auto-vacuum internal data" job
        alongside unrelated cleanup.

        Stage 2 of the two-stage lifecycle (#261). Deletes at SKELETON_PARAM,
        not at RETENTION_PARAM — by the time a row reaches here it has already
        been stripped to event_type + create_date + user_id by
        _minimise_auth_log. #219's docstring called anonymising "the obvious
        future variant if the policy changes"; #261 is that change, with the
        correction that the skeleton is pseudonymous rather than anonymous and
        therefore still needs a window of its own.
        """
        days = self._skeleton_days()
        if days <= 0:
            _logger.info(
                "Auth-log purge is disabled (%s = %s).", SKELETON_PARAM, days)
            return 0

        deadline = fields.Datetime.now() - timedelta(days=days)
        # Odoo REFUSES a commit from inside a test ("Cannot commit or rollback a
        # cursor from inside a test"), so the per-batch commit is gated on
        # actually running under a cron. ir.cron puts cron_id in the context —
        # the same signal ir.autovacuum uses to authorise itself. Outside a cron
        # (tests, an odoo shell call) the sweep simply runs in one transaction,
        # which is what a test wants anyway.
        under_cron = bool(self.env.context.get('cron_id'))
        total = 0
        capped = True
        for _batch in range(GC_MAX_BATCHES):
            rows = self.sudo().search(
                [('create_date', '<', deadline)],
                limit=GC_CHUNK_SIZE, order='create_date asc')
            if not rows:
                capped = False
                break
            count = len(rows)
            rows.unlink()
            total += count
            # Commit per batch. Without this the chunking is theatre: all
            # GC_MAX_BATCHES * GC_CHUNK_SIZE deletes stay in ONE transaction
            # (ir.cron commits once, after the job method returns), so the lock
            # and WAL footprint match a single unbounded DELETE — exactly what
            # the chunking above claims to avoid. _commit_progress also reports
            # progress to the cron scheduler as it goes.
            if under_cron:
                self.env['ir.cron']._commit_progress(processed=count)
            if count < GC_CHUNK_SIZE:
                capped = False
                break

        if capped:
            # Distinguishable in the log from "drained the backlog". The case
            # for a NAMED cron over @api.autovacuum is that an auditor can point
            # at it, which fails if a multi-day backlog looks identical to a
            # clean sweep.
            _logger.warning(
                "Auth-log retention hit its per-run cap of %s rows (%s batches "
                "of %s); rows older than %s day(s) remain and will be collected "
                "by the next run.",
                GC_MAX_BATCHES * GC_CHUNK_SIZE, GC_MAX_BATCHES,
                GC_CHUNK_SIZE, days)

        # Logged unconditionally, including the zero case: the audit story for
        # a retention policy is "it ran and here is what it did", and a silent
        # cron cannot be distinguished from one that never fired.
        _logger.info(
            "Auth-log retention: %s row(s) older than %s day(s) deleted "
            "(cutoff %s).", total, days, deadline)
        return total

    @api.model
    def _prepare_capture_vals(self, event_type, login=None, user=None):
        """Collect event + HTTP context. Safe with no HTTP request (shell/cron)."""
        ip_address = user_agent = None
        if request:
            httprequest = request.httprequest
            # With proxy_mode=True Odoo already resolved X-Forwarded-For, so
            # remote_addr is the real client IP behind the Nginx edge.
            ip_address = httprequest.remote_addr
            user_agent = (httprequest.user_agent.string or '')[:512]
        return {
            'event_type': event_type,
            'login': login or (user and user.login) or None,
            'user_id': user.id if user else None,
            'ip_address': ip_address,
            'user_agent': user_agent,
            'db_name': self.env.cr.dbname,
        }

    @api.model
    def _capture(self, event_type, login=None, user=None):
        """Log an event inside the current transaction (success paths)."""
        self.sudo().create(self._prepare_capture_vals(event_type, login=login, user=user))

    @api.model
    def _capture_isolated(self, event_type, login=None):
        """Log an event in its OWN cursor so it survives the caller's rollback.

        Used for login failures: the AccessDenied that follows rolls back the
        request transaction, which would erase a same-cursor log row.
        """
        vals = self._prepare_capture_vals(event_type, login=login)
        with self.env.registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            env['ncollection.auth.log'].create(vals)
            # Explicit commit on OUR OWN cursor — the sanctioned exception to
            # the never-commit rule (OCA contribution guide: committing is
            # legitimate on a cursor you created yourself). Required so the
            # row survives the caller's rollback.
            cr.commit()  # pylint: disable=invalid-commit
