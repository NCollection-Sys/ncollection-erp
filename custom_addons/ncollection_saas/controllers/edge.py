# -*- coding: utf-8 -*-
"""The edge agent's two calls into the PLATFORM database (custom tenant domains).

    GET  /ncollection/edge/v1/state    -> the custom domains that passed ownership verification (desired edge state)
    POST /ncollection/edge/v1/report   -> certificate / activation results for those domains

Authentication: ``Authorization: Bearer <key>`` where key = HMAC-SHA256(NC_CONFIG_SYNC_KEY, "nc-edge-v1:" || db).
The label differs from config-sync's, so neither key can be replayed as the other, and a tenant key (derived for a
tenant database) never opens this door. The agent runs on the same host as root and derives the key from the
platform's server-local .env; nothing is stored in a database. Wrong or missing key: 404, the same answer as a
database without this module, so the route is not discoverable.

These routes exist only where ncollection_saas is installed: the platform database. A tenant database has no such
module and answers 404 for both paths.
"""
import base64
import hashlib
import hmac
import json
import logging
import os

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

_MASTER_ENV = 'NC_CONFIG_SYNC_KEY'
_LABEL = b'nc-edge-v1:'
_MAX_BODY = 256 * 1024


def derive_edge_key(master, db):
    mac = hmac.new(master.encode(), _LABEL + db.encode(), hashlib.sha256)
    return base64.urlsafe_b64encode(mac.digest()).decode()


def _authorised():
    master = os.environ.get(_MASTER_ENV)
    header = request.httprequest.headers.get('Authorization', '')
    if not master or not header.startswith('Bearer ') or not request.db:
        return False
    return hmac.compare_digest(header[7:].strip().encode(), derive_edge_key(master, request.db).encode())


def _json(payload, status=200):
    return request.make_response(json.dumps(payload), status=status,
                                 headers=[('Content-Type', 'application/json'), ('Cache-Control', 'no-store')])


class EdgeController(http.Controller):

    @http.route('/ncollection/edge/v1/state', type='http', auth='none', methods=['GET'], csrf=False,
                save_session=False)
    def edge_state(self):
        if not _authorised():
            return request.not_found()
        env = request.env(su=True)
        return _json(env['ncollection.domain']._nc_edge_state())

    @http.route('/ncollection/edge/v1/report', type='http', auth='none', methods=['POST'], csrf=False,
                save_session=False, readonly=False)
    def edge_report(self):
        if not _authorised():
            return request.not_found()
        raw = request.httprequest.get_data(cache=False)[:_MAX_BODY + 1]
        if len(raw) > _MAX_BODY:
            return _json({'error': 'too large'}, 413)
        try:
            results = json.loads(raw or b'{}').get('results')
        except (ValueError, AttributeError):
            return _json({'error': 'malformed'}, 400)
        applied = request.env(su=True)['ncollection.domain']._nc_edge_report(results)
        return _json({'applied': applied})
