# -*- coding: utf-8 -*-
"""Anomaly alert records (P5-T04) — tenant-side.

One row per detected anomaly, carrying the severity and the suggested action
the ticket asks for. Nothing here reaches outside the tenant database: alerts
are ordinary tenant records, so database-per-tenant isolation is preserved by
construction rather than by a check.

**Idempotency is the whole design.** `ARCHITECTURE_DATA_PLATFORM`'s cron-hygiene
invariant requires every `ir.cron` to be idempotent, and this one runs daily
against a rolling window — so without deduplication the same Monday sales dip
would produce a fresh alert every day for as long as it stayed in the window.
`dedup_key` and its UNIQUE constraint make a re-run a no-op at the database
level, not merely by convention.
"""

import logging
import math

from markupsafe import escape
from psycopg2.errors import UniqueViolation

from odoo import SUPERUSER_ID, api, fields, models

from . import detectors as anomaly_detectors
from . import statistics as stats

_logger = logging.getLogger(__name__)

SEVERITY_SELECTION = [
    ('info', 'Info'),
    ('warning', 'Warning'),
    ('critical', 'Critical'),
]

# `zscore_of_latest` returns ±inf for the flat-then-moved case, which is correct
# arithmetic and unstorable in practice:
#
#   * The column cannot hold it. `zscore` is `digits=(16, 4)`, i.e. a
#     PostgreSQL NUMERIC(16,4), and NUMERIC has no infinity — verified:
#     `SELECT 'Infinity'::numeric(16,4)` raises "numeric field overflow". So
#     WITHOUT this clamp, the flat-then-moved case — the one the statistics
#     module calls the clearest signal there is — would raise on every write.
#   * JSON has no Infinity either. `json.dumps(float('inf'))` emits the bare
#     token `Infinity`, which is invalid JSON — so an alert with an infinite
#     z-score would break every jsonrpc caller that read it, including the
#     dashboards this feature exists to feed.
#   * The value adds nothing once `severity` is computed: everything at or above
#     SEVERITY_CRITICAL_AT is already 'critical'.
#
# So infinity is clamped on the way into the database and the semantic is
# carried by `severity`. The cap is far above any finite deviation a real series
# produces, which keeps ordering by |zscore| honest.
ZSCORE_CAP = 9999.0


def storable_zscore(zscore):
    """Clamp a z-score to something a database column and JSON can both hold."""
    if zscore is None:
        return 0.0
    if math.isinf(zscore):
        return math.copysign(ZSCORE_CAP, zscore)
    if math.isnan(zscore):  # pragma: no cover - defensive
        return 0.0
    return max(-ZSCORE_CAP, min(ZSCORE_CAP, zscore))


class NCollectionAlert(models.Model):
    """A detected anomaly, with enough context for a human to act on it."""

    _name = 'ncollection.alert'
    _description = 'NCollection Anomaly Alert'
    # Newest and most severe first: this is read as a worklist, and 'critical'
    # sorts before 'info' only by luck of the alphabet, so severity is ordered
    # explicitly via the stored sequence below.
    _order = 'detected_at desc, severity_rank asc, id desc'

    name = fields.Char(required=True, help="One-line human summary.")
    detector_key = fields.Char(
        required=True, index=True,
        help="Which detector produced this, e.g. 'stock_below_safety'.")
    severity = fields.Selection(SEVERITY_SELECTION, required=True, index=True)
    # Stored rather than computed-on-the-fly so _order can use it. A Selection
    # sorts by its stored string, which would put 'critical' before 'info'
    # before 'warning' — alphabetical, not meaningful.
    severity_rank = fields.Integer(
        compute='_compute_severity_rank', store=True, index=True)
    suggested_action = fields.Text(
        help="What a human should do about it. Written by the detector, "
             "because the detector is the only thing that knows why it fired.")
    detected_at = fields.Datetime(
        required=True, default=fields.Datetime.now, index=True)

    observed_value = fields.Float(help="The value that triggered the alert.")
    baseline_value = fields.Float(help="Mean of the trailing baseline.")
    zscore = fields.Float(
        digits=(16, 4),
        help="Standard deviations from the baseline. Clamped — see ZSCORE_CAP.")

    state = fields.Selection(
        [('new', 'New'), ('acknowledged', 'Acknowledged')],
        default='new', required=True, index=True,
        help="Acknowledged alerts stay for audit but drop out of the digest.")

    # The idempotency key. Built by the detector from (detector, scope, period)
    # so that re-running the cron over the same window cannot duplicate a row.
    # No index=True: the UNIQUE constraint below already creates a btree,
    # and a second one on the same column is pure write cost.
    dedup_key = fields.Char(required=True, copy=False)

    _dedup_key_uniq = models.Constraint(
        'UNIQUE (dedup_key)',
        'An alert for this detector, subject and period already exists.')

    @api.depends('severity')
    def _compute_severity_rank(self):
        ranks = {'critical': 0, 'warning': 1, 'info': 2}
        for alert in self:
            alert.severity_rank = ranks.get(alert.severity, 99)

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------

    @api.model
    def _record_finding(self, finding):
        """Create one alert from a detector finding, or return an empty set.

        Fail-soft by design, matching `ncollection_core`'s governing principle
        (see `license_enforcement.py`): a detector that cannot record its
        finding must not take down the cron for the detectors after it.

        A duplicate is a NORMAL outcome, not an error — it is what idempotency
        looks like on the second run — so it is caught narrowly and logged at
        debug. Everything else is a real bug and is logged as one.
        """
        # Two kinds of detector share this path, on purpose:
        #   * statistical ones supply a z-score and let the ladder decide;
        #   * threshold ones ("stock below its safety level") supply severity
        #     directly, because being under a configured minimum is a fact, not
        #     a deviation, and forcing it through a z-score would be contrived.
        # Both end up in one model so the digest and the UI have one worklist.
        severity = finding.get('severity') or stats.severity_for(finding.get('zscore'))
        if not severity:
            return self.browse()

        values = {
            'name': finding['name'],
            'detector_key': finding['detector_key'],
            'severity': severity,
            'suggested_action': finding.get('suggested_action'),
            'observed_value': finding.get('observed_value') or 0.0,
            'baseline_value': finding.get('baseline_value') or 0.0,
            'zscore': storable_zscore(finding.get('zscore')),
            'dedup_key': finding['dedup_key'],
        }

        # Savepoint per row: a UNIQUE violation aborts the current transaction
        # in PostgreSQL, and without this the whole cron batch would be lost on
        # the first duplicate — which, given the dedup key, is the common case.
        try:
            with self.env.cr.savepoint():
                return self.create(values)
        except UniqueViolation:
            # The EXPECTED outcome of a re-run. This is what idempotency looks
            # like, so it is not news.
            _logger.debug("Alert %s already recorded.", finding.get('dedup_key'))
            return self.browse()
        except Exception:  # noqa: BLE001 - never break the cron
            # Anything else is a real bug, and it must not be filed under
            # "probably a duplicate" at a level nobody reads. An earlier version
            # logged every failure at DEBUG with that wording, which meant a
            # detector emitting an invalid value (raising in the ORM, before any
            # SQL) silently dropped a genuine anomaly with no trace in
            # production — undermining the zero-false-negative guarantee this
            # feature is built around.
            _logger.exception(
                "Anomaly alert could not be recorded (detector=%s, key=%s)",
                finding.get('detector_key'), finding.get('dedup_key'))
            return self.browse()

    # ------------------------------------------------------------------
    # Scheduled detection
    # ------------------------------------------------------------------

    @api.model
    def _cron_detect_anomalies(self):
        """Run every detector, one batch per detector, inside a time budget.

        **Why the batching is not optional here.** A tenant database has NO
        queue runner: `queue_job` is installed on the admin DB only
        (CORE_TENANT_MODULES is base / ncollection_core / ncollection_branding /
        ncollection_auth). So `ARCHITECTURE_DATA_PLATFORM`'s cron-hygiene
        invariant — "anything > 30 s of work belongs on the queue runner" — has
        nothing to escalate to, and this cron must self-limit instead.

        That matters at fleet scale, not on one database: in the pooling
        topology `odoo-bus` runs the crons of EVERY tenant on two threads, so an
        unbounded detector here would starve every other tenant's crons. #310 is
        the same failure with one outbound fetch; this would be the same shape
        multiplied by tenant count.

        `_commit_progress` is Odoo 19's native answer: it commits what is done,
        reports what remains, and returns the seconds left in this run. When the
        budget is gone we stop, and Odoo reschedules the job as PARTIALLY_DONE
        rather than letting it overrun.

        **What that does and does not buy, precisely.** A rescheduled run calls
        this method again from the top — there is no per-detector "already ran"
        marker, so the cheap detectors are re-evaluated. `dedup_key` makes that
        harmless for the RECORDS (no duplicates) but the queries are redone.
        The expensive detector is the stock one, and it carries its own
        resumable cursor (`detectors.STOCK_CURSOR_PARAM`) so its progress does
        survive across runs. Under sustained pressure the ordering here still
        favours the earlier detectors; if per-detector cost ever grows, this
        wants a rotating start offset rather than a fixed list.
        """
        cron = self.env['ir.cron']
        detector = self.env['ncollection.anomaly.detector']
        keys = list(anomaly_detectors.DETECTOR_KEYS)

        # Declare the size of the job up front so progress is meaningful.
        cron._commit_progress(remaining=len(keys))

        for key in keys:
            for finding in detector.detect(key):
                self._record_finding(finding)
            # One detector = one committed step.
            seconds_left = cron._commit_progress(1)
            if not seconds_left:
                _logger.info(
                    "Anomaly detection paused after %r; Odoo will reschedule "
                    "the remaining detectors.", key)
                break
        return True

    # ------------------------------------------------------------------
    # Digest
    # ------------------------------------------------------------------

    @api.model
    def _cron_send_digest(self):
        """Email the unacknowledged alerts, newest first.

        `mail` is soft-checked rather than declared in `depends`, matching the
        rule the aggregation engine sets for `sale`/`account`/`stock`/`hr`: this
        module must not be able to force a module into a tenant's set. `mail` is
        installed on tenants today, so this is belt-and-braces — but the check
        costs one line and the alternative silently changes what provisioning
        installs.
        """
        if 'mail.mail' not in self.env:
            _logger.info("Alert digest skipped: mail is not installed.")
            return False

        sent = 0
        for user in self._digest_recipients():
            # THE POINT: the recipient's OWN rights select the content. Running
            # the search `with_user` makes the ORM apply the record rules from
            # security/alert_security.xml, so the mail contains exactly what
            # that person could see in the UI — no more, and no less.
            #
            # The alternative — a detector_key -> role map here — was rejected.
            # It would duplicate the rules, and the two would drift: the day
            # they disagreed, this cron would email somebody a slice they are
            # not allowed to open. Deriving the content from the rules makes
            # that class of bug unrepresentable.
            #
            # It is also per-RECIPIENT rather than per-ROLE, which is what makes
            # a CEO-who-is-also-in-sales get one correct digest instead of two
            # partial ones.
            alerts = self.with_user(user).search(
                [('state', '=', 'new')], order='severity_rank, detected_at desc')
            if not alerts:
                continue      # nothing for this person: send nothing at all

            # ESCAPED, not interpolated raw. `alert.name` and
            # `suggested_action` embed values the detector took from tenant
            # data — `_detect_stock_below_safety` puts the product and
            # warehouse NAMES straight into them — so they are user-authored
            # strings reaching an HTML email. #346 widened the audience from
            # administrators to seven role groups, which is the moment a
            # mis-named product stops being a curiosity.
            lines = ''.join(
                '<li><b>%s</b> — %s<br/><i>%s</i></li>' % (
                    escape(dict(SEVERITY_SELECTION).get(alert.severity,
                                                        alert.severity)),
                    escape(alert.name or ''),
                    escape(alert.suggested_action or ''),
                )
                for alert in alerts
            )
            body = '<p>Open anomaly alerts:</p><ul>%s</ul>' % lines
            # QUEUED, NOT SENT. `.send()` here would do a synchronous SMTP
            # round-trip per recipient, on the cron thread, with no budget —
            # and a tenant database has no queue_job runner to escalate to
            # while `odoo-bus` runs EVERY tenant's crons on two threads. That
            # is #310's failure class exactly, and an earlier revision of this
            # method had it: one mail per run became one blocking send per role
            # holder. `mail.mail` defaults to state 'outgoing', and Odoo's own
            # `ir_cron_mail_scheduler_action` flushes the queue in batched,
            # budgeted runs off this thread. Creating the record IS the work
            # this cron owns; delivering it is not.
            self.env['mail.mail'].sudo().create({
                'subject': 'NCollection: %s open alert(s)' % len(alerts),
                'body_html': body,
                'email_to': user.email,
                'auto_delete': True,
            })
            sent += 1

            # Yield to the run's budget between recipients, the same way
            # _cron_detect_anomalies does. Cheap now that no SMTP happens here,
            # but it keeps the loop bounded on a tenant with many role holders
            # rather than relying on that number staying small.
            if not self.env['ir.cron']._commit_progress(1):
                _logger.info(
                    "Alert digest paused after %s recipient(s); Odoo will "
                    "reschedule the rest.", sent)
                break

        if not sent:
            _logger.info("Alert digest: nothing to send to anyone.")
        return bool(sent)

    @api.model
    def _digest_recipients(self):
        """The users who should be told about alerts, as a recordset.

        Everyone who can SEE an alert, which after #346 means the role holders
        as well as administrators — a role that is trusted with the data is a
        role worth notifying. Still not "every internal user": a plain employee
        has no ACL on the model, so there is nothing to tell them about.

        Returns users rather than addresses because the caller runs the search
        `with_user`, letting the record rules decide each person's contents.
        """
        # Every group that any alert record rule grants, plus system. Derived
        # from the SAME xmlids the rules use, so adding a role to a rule and
        # forgetting the digest cannot happen silently — a role that can see
        # alerts is a role that gets told about them.
        #
        # Membership is read via `all_user_ids`, so implication counts: an
        # owner holds these through ceo/system without being listed anywhere.
        #
        # `all_user_ids`, NOT `users`: Odoo 19 renamed the members field — the
        # mirror of the `res.users.groups_id` -> `group_ids` rename CLAUDE.md
        # records — and `res.groups.users` no longer exists, so the old
        # spelling raised AttributeError on every digest run. That bug shipped
        # in #61 and was invisible until the method got a test.
        group_xmlids = (
            'base.group_system',
            'ncollection_core.group_role_sales',
            'ncollection_core.group_role_accountant',
            'ncollection_core.group_role_hr',
            'ncollection_core.group_role_warehouse',
            'ncollection_core.group_role_manager',
            'ncollection_core.group_role_ceo',
            'ncollection_core.group_role_owner',
        )
        users = self.env['res.users'].browse()
        for xmlid in group_xmlids:
            group = self.env.ref(xmlid, raise_if_not_found=False)
            if group:
                users |= group.sudo().all_user_ids
        # Deduplicated by the recordset union above, so a user in three roles
        # still receives exactly one digest.
        #
        # `id != SUPERUSER_ID` is defence in depth, not a live fix. The whole
        # design rests on `with_user(user)` DROPPING superuser — verified in
        # Odoo 19's `Environment.__call__`, which recomputes `su` as False once
        # a concrete user is passed. But `Environment.__new__` forces `su=True`
        # again whenever `uid == 1`, so the reserved superuser record is the one
        # account for which the per-recipient search would silently return
        # EVERYTHING. It ships inactive and without an email, so the filter
        # below already excludes it — this makes that non-accidental.
        return users.filtered(
            lambda u: u.email and u.active and u.id != SUPERUSER_ID)
