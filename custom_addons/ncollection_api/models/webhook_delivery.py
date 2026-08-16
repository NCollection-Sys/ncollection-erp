import logging
import time
import uuid
from datetime import timedelta
import requests

from odoo import fields, models

_logger = logging.getLogger(__name__)

# Exponential retry backoff intervals in seconds (1m, 5m, 30m, 2h)
RETRY_BACKOFF_INTERVALS = [60, 300, 1800, 7200]
MAX_DELIVERY_ATTEMPTS = 5
MAX_SUBSCRIPTION_FAILURES_BEFORE_DEGRADED = 5
MAX_SUBSCRIPTION_FAILURES_BEFORE_DISABLED = 15


class NCollectionWebhookDelivery(models.Model):
    """Delivery log and retry tracker for webhook events (P8-T03).

    Tracks delivery attempts, HTTP response status, execution latency, and
    exponential backoff schedules for guaranteed at-least-once delivery.
    """
    _name = 'ncollection.webhook.delivery'
    _description = 'NCollection Webhook Delivery Attempt'
    _order = 'create_date desc, id desc'

    uuid = fields.Char(
        default=lambda self: str(uuid.uuid4()),
        required=True,
        readonly=True,
        index=True,
    )
    subscription_id = fields.Many2one(
        'ncollection.webhook.subscription',
        required=True,
        ondelete='cascade',
        index=True,
    )
    event = fields.Char(string="Event Name", required=True, index=True)
    payload = fields.Text(string="Payload JSON", required=True)
    state = fields.Selection(
        [
            ('pending', 'Pending'),
            ('delivered', 'Delivered'),
            ('failed', 'Failed'),
            ('dead_letter', 'Dead Letter'),
        ],
        default='pending',
        required=True,
        index=True,
    )
    attempt = fields.Integer(string="Attempt Count", default=0, readonly=True)
    max_attempts = fields.Integer(default=MAX_DELIVERY_ATTEMPTS)
    next_retry = fields.Datetime(index=True)
    response_code = fields.Integer(readonly=True)
    response_body = fields.Text(readonly=True)
    duration_ms = fields.Float(string="Duration (ms)", readonly=True)
    error_message = fields.Text(string="Error Details", readonly=True)
    delivered_at = fields.Datetime(readonly=True)

    def _nc_deliver(self, timeout=10):
        """Execute the HTTP POST delivery attempt for this record."""
        self.ensure_one()
        sub = self.subscription_id
        if not sub or not sub.target_url:
            self.write({
                'state': 'dead_letter',
                'error_message': 'Subscription or target URL is missing.',
            })
            return False

        epoch_time = str(int(time.time()))
        payload_str = self.payload or '{}'
        signature = sub._nc_sign_payload(epoch_time, payload_str)

        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'NCollection-Webhook-Delivery/1.0',
            'X-NCollection-Delivery': self.uuid,
            'X-NCollection-Event': self.event,
            'X-NCollection-Timestamp': epoch_time,
            'X-NCollection-Signature': signature,
        }

        start_time = time.time()
        new_attempt = self.attempt + 1
        resp_code = 0
        resp_body = ''
        err_msg = ''
        success = False

        try:
            resp = requests.post(
                sub.target_url,
                data=payload_str.encode('utf-8'),
                headers=headers,
                timeout=timeout,
                allow_redirects=False,
            )
            duration_ms = (time.time() - start_time) * 1000.0
            resp_code = resp.status_code
            resp_body = (resp.text or '')[:1024]
            if 200 <= resp_code < 300:
                success = True
            else:
                err_msg = f"HTTP {resp_code}: {resp_body[:200]}"
        except Exception as exc:
            duration_ms = (time.time() - start_time) * 1000.0
            err_msg = f"{type(exc).__name__}: {str(exc)}"

        now = fields.Datetime.now()
        if success:
            self.write({
                'state': 'delivered',
                'attempt': new_attempt,
                'response_code': resp_code,
                'response_body': resp_body,
                'duration_ms': duration_ms,
                'error_message': False,
                'delivered_at': now,
                'next_retry': False,
            })
            # Reset subscription failure counter
            if sub.failure_count > 0 or sub.state == 'degraded':
                sub.write({'failure_count': 0, 'state': 'active'})
            _logger.info(
                "Webhook delivery %s for event '%s' succeeded (HTTP %s in %.2fms)",
                self.uuid, self.event, resp_code, duration_ms
            )
            return True
        else:
            is_dead_letter = new_attempt >= self.max_attempts
            next_retry_dt = False
            if not is_dead_letter:
                backoff_idx = min(new_attempt - 1, len(RETRY_BACKOFF_INTERVALS) - 1)
                delay_sec = RETRY_BACKOFF_INTERVALS[backoff_idx]
                next_retry_dt = now + timedelta(seconds=delay_sec)

            self.write({
                'state': 'dead_letter' if is_dead_letter else 'failed',
                'attempt': new_attempt,
                'response_code': resp_code,
                'response_body': resp_body,
                'duration_ms': duration_ms,
                'error_message': err_msg,
                'next_retry': next_retry_dt,
            })

            # Update subscription health
            new_sub_failures = sub.failure_count + 1
            sub_vals = {'failure_count': new_sub_failures}
            if new_sub_failures >= MAX_SUBSCRIPTION_FAILURES_BEFORE_DISABLED:
                sub_vals['state'] = 'disabled'
            elif new_sub_failures >= MAX_SUBSCRIPTION_FAILURES_BEFORE_DEGRADED:
                sub_vals['state'] = 'degraded'
            sub.write(sub_vals)

            _logger.warning(
                "Webhook delivery %s for event '%s' failed (attempt %d/%d): %s",
                self.uuid, self.event, new_attempt, self.max_attempts, err_msg
            )
            return False

    def action_retry(self):
        """Manually trigger an immediate retry for a failed or dead-letter delivery."""
        for rec in self:
            rec.write({
                'state': 'pending',
                'next_retry': fields.Datetime.now(),
            })
            rec._nc_deliver()
        return True
