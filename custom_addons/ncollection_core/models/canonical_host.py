# -*- coding: utf-8 -*-
"""Redirect a browser to the workspace's canonical address (custom tenant domains, opt-in per tenant).

Set by the platform through ``ncollection.admin.service.set_canonical_host``: ``ncollection.canonical_host`` (the
primary address), ``ncollection.host_aliases`` (every hostname allowed to serve this workspace) and
``ncollection.canonical_redirect``. When redirect is on, a GET/HEAD page request on another ALIAS gets a 301 to the
same path on the canonical host. Never redirected: a host that is not an alias (nothing is ever sent to a name the
platform did not verify), machine traffic (JSON-RPC, JSON-2, websocket/longpolling, health, the release and edge
channels, assets), and anything but GET/HEAD - so integrations, the platform's own service calls and sessions in
flight keep working on the address they used.
"""
import werkzeug.exceptions
import werkzeug.utils

from odoo import models
from odoo.http import request

from .admin_service import CANONICAL_HOST_PARAM, CANONICAL_REDIRECT_PARAM, HOST_ALIASES_PARAM

_NEVER_PREFIXES = ('/json/', '/jsonrpc', '/xmlrpc', '/web/dataset/', '/web/action/', '/websocket', '/longpolling',
                   '/web/health', '/web/assets/', '/web/static/', '/web/webclient/', '/ncollection/release',
                   '/ncollection/edge/', '/api/', '/mail/', '/bus/')


class IrHttpCanonicalHost(models.AbstractModel):
    _inherit = 'ir.http'

    @classmethod
    def _nc_canonical_redirect_target(cls, httprequest, params):
        if httprequest.method not in ('GET', 'HEAD'):
            return None
        if params.get_param(CANONICAL_REDIRECT_PARAM) != 'True':
            return None
        canonical = (params.get_param(CANONICAL_HOST_PARAM) or '').strip().lower()
        aliases = {a for a in (params.get_param(HOST_ALIASES_PARAM) or '').split(',') if a}
        host = (httprequest.host or '').partition(':')[0].strip().lower()
        if not canonical or canonical not in aliases or host == canonical or host not in aliases:
            return None
        path = httprequest.path or '/'
        if path.startswith(_NEVER_PREFIXES):
            return None
        query = httprequest.query_string.decode('latin-1') if httprequest.query_string else ''
        return 'https://%s%s%s' % (canonical, path, ('?' + query) if query else '')

    @classmethod
    def _pre_dispatch(cls, rule, args):
        super()._pre_dispatch(rule, args)
        if request is None or not getattr(request, 'env', None):
            return
        target = cls._nc_canonical_redirect_target(
            request.httprequest, request.env['ir.config_parameter'].sudo())
        if target:
            werkzeug.exceptions.abort(werkzeug.utils.redirect(target, code=301))
