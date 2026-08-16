import json
import logging
from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class NCollectionWebhookDispatcher(models.AbstractModel):
    """Event dispatcher and cron processor for outgoing webhooks (P8-T03)."""
    _name = 'ncollection.webhook.dispatcher'
    _description = 'NCollection Webhook Event Dispatcher'

    @api.model
    def dispatch_event(self, event_name, payload_dict, sync=True):
        """Dispatch a business event to all matching active webhook subscriptions.

        :param event_name: str, e.g. 'sale.order.confirmed', 'invoice.posted'
        :param payload_dict: dict, event data payload
        :param sync: bool, whether to attempt immediate HTTP delivery
        :return: recordset of created ncollection.webhook.delivery records
        """
        Sub = self.env['ncollection.webhook.subscription']
        Delivery = self.env['ncollection.webhook.delivery']

        subscriptions = Sub.search([
            ('active', '=', True),
            ('state', '!=', 'disabled'),
        ])
        matching_subs = subscriptions.filtered(lambda s: s._nc_matches_event(event_name))
        if not matching_subs:
            return Delivery

        payload_json = json.dumps(payload_dict, default=str)
        created_deliveries = Delivery

        for sub in matching_subs:
            delivery = Delivery.create({
                'subscription_id': sub.id,
                'event': event_name,
                'payload': payload_json,
                'state': 'pending',
                'next_retry': fields.Datetime.now(),
            })
            created_deliveries |= delivery
            if sync:
                try:
                    delivery._nc_deliver()
                except Exception as exc:
                    _logger.error(
                        "Webhook sync dispatch error for delivery %s: %s",
                        delivery.uuid, str(exc)
                    )

        return created_deliveries

    @api.model
    def _nc_cron_process_pending_webhooks(self, batch_size=50):
        """Background cron job to process pending and scheduled retry webhook deliveries."""
        Delivery = self.env['ncollection.webhook.delivery']
        now = fields.Datetime.now()

        pending_deliveries = Delivery.search([
            ('state', 'in', ('pending', 'failed')),
            '|',
            ('next_retry', '=', False),
            ('next_retry', '<=', now),
        ], limit=batch_size, order='next_retry asc, id asc')

        if not pending_deliveries:
            return True

        _logger.info("Processing %d pending webhook deliveries...", len(pending_deliveries))
        for delivery in pending_deliveries:
            try:
                delivery._nc_deliver()
            except Exception as exc:
                _logger.error("Error delivering webhook %s in cron: %s", delivery.uuid, str(exc))

        return True
