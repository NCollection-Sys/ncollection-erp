# -*- coding: utf-8 -*-
"""The ASP callback endpoint: ``POST /ncollection/einvoice/callback/<provider id>``.

The database is the request host's (db_filter); there is no session and no user. Authenticity is the ADAPTER's
verdict (signature, freshness, replay: lib/adapter_contract.py), asked BEFORE the body is parsed; an unverified
callback changes nothing and learns nothing (the same 401 for a wrong provider, a wrong signature or a disabled
module). Every accepted event is stored once (provider, message id), so a repeated delivery is harmless.
"""

import logging

from odoo import SUPERUSER_ID, http
from odoo.http import request

from ..lib import adapter_contract as contract

_logger = logging.getLogger(__name__)

MAX_BODY = 1024 * 1024  # a status callback is small; anything larger is refused before it is read


def _reply(status):
    return request.make_response('', status=status, headers=[('Cache-Control', 'no-store')])


class EInvoiceCallback(http.Controller):

    # csrf=False: the adapter-verified signature IS the credential. readonly=False: auth='none' routes are read-only
    # by default (CLAUDE.md, Odoo 19 gotchas) and an event must be stored.
    @http.route('/ncollection/einvoice/callback/<int:provider_id>', type='http', auth='none', methods=['POST'],
                csrf=False, readonly=False, save_session=False, sitemap=False)
    def callback(self, provider_id, **_ignored):
        if not request.db:
            return _reply(404)
        length = request.httprequest.content_length
        if length is not None and length > MAX_BODY:
            return _reply(413)
        body = request.httprequest.get_data(cache=False)[:MAX_BODY + 1]
        if len(body) > MAX_BODY:
            return _reply(413)
        env = request.env(user=SUPERUSER_ID, su=True)
        if 'ncollection.einvoice.provider' not in env:
            return _reply(401)
        provider = env['ncollection.einvoice.provider'].browse(provider_id).exists()
        if not provider or not provider.adapter:
            return _reply(401)
        headers = {key.lower(): value for key, value in request.httprequest.headers.items()}
        try:
            adapter = env['ncollection.einvoice']._nc_adapter(provider)
            if not adapter.verify_callback(headers, body):
                _logger.warning('E-invoice callback refused for provider %s: not verified', provider.id)
                return _reply(401)
            events = adapter.parse_callback(headers, body)
        except contract.AdapterError as exc:
            _logger.warning('E-invoice callback for provider %s not accepted: %s', provider.id, exc.category.value)
            return _reply(400 if exc.category == contract.ErrorCategory.PERMANENT else 503)
        except Exception:  # noqa: BLE001 - an adapter bug must not become a 500 with a traceback for the caller
            _logger.exception('E-invoice callback adapter failure for provider %s', provider.id)
            return _reply(503)
        env['ncollection.einvoice.event']._nc_ingest(provider, list(events or ()), 'callback')
        return _reply(204)
