# -*- coding: utf-8 -*-
"""P8-T01: request logging — metadata only, never bodies.

The stance is `ncollection_auth`'s (#219/#261) and the AI gateway's
(ARCHITECTURE_SECURITY §11: "logs metadata only — never prompt or completion
bodies"). A request log that stores payloads becomes a second copy of the
customer data the API serves, with different ACLs and a different retention
policy — which is exactly the mistake #81 caught when blanket auditing swept a
live signup token into a lower-trust table.

So: who, when, which route, which status, how long. Never the request body,
never the response body, never the Authorization header.

DOUBLE DUTY. These rows are also the rate limiter's counter — a request per
row, counted over a window. One write instead of two, and the limiter cannot
disagree with the log about what happened.
"""
from odoo import fields, models


class NcollectionApiRequestLog(models.Model):
    _name = 'ncollection.api.request.log'
    _description = 'NCollection API Request Log'
    _order = 'id desc'

    client_id = fields.Many2one(
        'ncollection.api.client', index=True, ondelete='set null',
        help="Null for a request that failed before the client was known — "
             "which is itself worth keeping.")
    route = fields.Char(readonly=True, index=True)
    method = fields.Char(readonly=True)
    status = fields.Integer(readonly=True, index=True)
    remote_addr = fields.Char(readonly=True)
    duration_ms = fields.Integer(readonly=True)
    scope_checked = fields.Char(
        readonly=True,
        help="The scope the route required, so a 403 can be explained without "
             "re-deriving it from code.")
