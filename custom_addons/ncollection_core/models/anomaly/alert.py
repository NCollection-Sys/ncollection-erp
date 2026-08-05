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

from odoo import api, fields, models

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
#   * JSON has no Infinity. `json.dumps(float('inf'))` emits the bare token
#     `Infinity`, which is invalid JSON — so an alert with an infinite z-score
#     would break every jsonrpc caller that read it, including the dashboards
#     this feature exists to feed.
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
    dedup_key = fields.Char(required=True, index=True, copy=False)

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
        looks like on the second run — so it is caught and logged at debug.
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
        except Exception as exc:  # noqa: BLE001 - never break the cron
            _logger.debug(
                "Alert not recorded for %s (likely already present): %s",
                finding.get('dedup_key'), exc)
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
        budget is gone we stop, and Odoo reschedules the job ASAP as
        PARTIALLY_DONE rather than letting it overrun. Work already committed is
        never redone thanks to `dedup_key`.
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

        alerts = self.search([('state', '=', 'new')], order='severity_rank, detected_at desc')
        if not alerts:
            return False

        recipients = self._digest_recipients()
        if not recipients:
            _logger.info("Alert digest skipped: no recipient with an email.")
            return False

        lines = ''.join(
            '<li><b>%s</b> — %s<br/><i>%s</i></li>' % (
                dict(SEVERITY_SELECTION).get(alert.severity, alert.severity),
                alert.name,
                alert.suggested_action or '',
            )
            for alert in alerts
        )
        self.env['mail.mail'].sudo().create({
            'subject': 'NCollection: %s open alert(s)' % len(alerts),
            'body_html': '<p>Open anomaly alerts:</p><ul>%s</ul>' % lines,
            'email_to': ','.join(recipients),
            'auto_delete': True,
        }).send()
        return True

    @api.model
    def _digest_recipients(self):
        """Emails of the people who should act on alerts.

        System administrators of this tenant. Deliberately not "every internal
        user": an alert digest sent to everyone is a digest everyone filters.
        """
        group = self.env.ref('base.group_system', raise_if_not_found=False)
        if not group:
            return []
        return [user.email for user in group.sudo().users if user.email]
