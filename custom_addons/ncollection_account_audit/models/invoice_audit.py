# -*- coding: utf-8 -*-
"""The invoice amount audit entry (P8-T05 / #81 acceptance: "changing an invoice amount produces an audit entry with
old/new values, user, IP, and timestamp").

One row = one NET change of an invoice's amounts caused by one outermost ORM operation. Rows are evidence:

* never edited or deleted through the ORM (``write`` / ``unlink`` raise, the ACL grants read only, and the service
  creates rows with ``sudo``), and a content digest lets ``nc_verify`` find a row altered underneath the ORM;
* identity survives the invoice: the invoice id, number, company and the actor's login are copied as plain values, so a
  deleted draft or an archived user does not blank the trail;
* they hold amounts and identifiers only, and nothing here is ever written to the server log.
"""
import hashlib
import json

from odoo import api, fields, models
from odoo.exceptions import UserError

try:
    from odoo.http import request
except ImportError:  # pragma: no cover
    request = None

EVENTS = [
    ('created', 'Created'),
    ('amount_changed', 'Amount changed'),
    ('deleted', 'Deleted'),
]
# The decimals every amount is formatted with before it enters the digest, so the writer and the verifier agree.
_DIGEST_DECIMALS = '%.4f'


def client_ip():
    """The caller's address when there is an HTTP request; EMPTY otherwise (cron, shell, tests): inventing 127.0.0.1
    would look like a login from the server."""
    try:
        if request and request.httprequest:
            return request.httprequest.remote_addr or ''
    except RuntimeError:  # no request bound to this thread: the answer is the empty string below
        return ""
    return ""


class InvoiceAmountAudit(models.Model):
    _name = 'ncollection.invoice.audit'
    _description = 'Invoice amount audit entry'
    _order = 'logged_at desc, id desc'
    _rec_name = 'move_name'

    event = fields.Selection(EVENTS, required=True, readonly=True, index=True)
    # Identity of the invoice. `move_id` links while the invoice exists; `res_id` and `move_name` never change.
    move_id = fields.Many2one('account.move', string='Invoice', readonly=True, ondelete='set null', index=True)
    res_id = fields.Integer(string='Invoice ID', readonly=True, index=True)
    move_name = fields.Char(string='Invoice number', readonly=True)
    move_type = fields.Char(readonly=True)
    state = fields.Char(readonly=True)
    partner_name = fields.Char(string='Partner', readonly=True)
    company_id = fields.Many2one('res.company', required=True, readonly=True, ondelete='restrict', index=True)
    currency_id = fields.Many2one('res.currency', readonly=True)
    # Actor: the user row may be archived or removed later, the login stays.
    user_id = fields.Many2one('res.users', readonly=True, ondelete='set null')
    user_login = fields.Char(string='User login', readonly=True)
    ip_address = fields.Char(string='IP address', readonly=True)
    logged_at = fields.Datetime(string='Timestamp', required=True, readonly=True, index=True)
    # What triggered it, as a model name only ("account.move" or "account.move.line").
    trigger = fields.Char(readonly=True)
    old_amount_untaxed = fields.Monetary(readonly=True)
    old_amount_tax = fields.Monetary(readonly=True)
    old_amount_total = fields.Monetary(readonly=True)
    new_amount_untaxed = fields.Monetary(readonly=True)
    new_amount_tax = fields.Monetary(readonly=True)
    new_amount_total = fields.Monetary(readonly=True)
    old_lines_total = fields.Monetary(string='Old lines total', readonly=True)
    new_lines_total = fields.Monetary(string='New lines total', readonly=True)
    digest = fields.Char(readonly=True, copy=False)

    # ------------------------------------------------------------------ evidence
    @api.model
    def _nc_digest_values(self, vals):
        """The exact, ordered material of the digest, from a dict of field values (create) or a record's read."""
        def amount(key):
            return _DIGEST_DECIMALS % float(vals.get(key) or 0.0)

        def ident(key):
            value = vals.get(key)
            return value.id if hasattr(value, 'id') else (value or 0)

        logged = vals.get('logged_at')
        return [
            vals.get('event') or '', ident('res_id'), vals.get('move_name') or '', vals.get('state') or '',
            ident('company_id'), ident('currency_id'), ident('user_id'), vals.get('user_login') or '',
            vals.get('ip_address') or '', vals.get('trigger') or '',
            fields.Datetime.to_string(logged) if logged else '',
            amount('old_amount_untaxed'), amount('old_amount_tax'), amount('old_amount_total'),
            amount('new_amount_untaxed'), amount('new_amount_tax'), amount('new_amount_total'),
            amount('old_lines_total'), amount('new_lines_total'),
        ]

    @api.model
    def _nc_row_digest(self, vals):
        material = json.dumps(self._nc_digest_values(vals), separators=(',', ':'), ensure_ascii=False)
        return hashlib.sha256(material.encode('utf-8')).hexdigest()

    def nc_verify(self):
        """Ids of the rows whose stored content no longer matches their digest (altered underneath the ORM)."""
        bad = []
        for row in self.sudo():
            stored = {name: row[name] for name in (
                'event', 'res_id', 'move_name', 'state', 'company_id', 'currency_id', 'user_id', 'user_login',
                'ip_address', 'trigger', 'logged_at', 'old_amount_untaxed', 'old_amount_tax', 'old_amount_total',
                'new_amount_untaxed', 'new_amount_tax', 'new_amount_total', 'old_lines_total', 'new_lines_total')}
            if self._nc_row_digest(stored) != row.digest:
                bad.append(row.id)
        return bad

    # ------------------------------------------------------------------ write path (service only)
    @api.model
    def _nc_record(self, event, before, after, trigger):
        """Create one entry. ``before`` / ``after`` are snapshot dicts from ``account.move._nc_audit_snapshot``
        (``None`` when there is no such side)."""
        ref = after or before
        user = self.env.user
        vals = {
            'event': event,
            'res_id': ref['id'],
            'move_id': after and ref['id'] or False,
            'move_name': ref['name'],
            'move_type': ref['move_type'],
            'state': ref['state'],
            'partner_name': ref['partner'],
            'company_id': ref['company_id'],
            'currency_id': ref['currency_id'],
            'user_id': user.id,
            'user_login': user.login,
            'ip_address': client_ip(),
            'logged_at': fields.Datetime.now(),
            'trigger': trigger,
        }
        for side, snap in (('old', before), ('new', after)):
            for key in ('untaxed', 'tax', 'total'):
                vals['%s_amount_%s' % (side, key)] = snap['amount_' + key] if snap else 0.0
        vals['old_lines_total'] = before['lines_total'] if before else 0.0
        vals['new_lines_total'] = after['lines_total'] if after else 0.0
        vals['digest'] = self._nc_row_digest(vals)
        return self.sudo().create(vals)

    # ------------------------------------------------------------------ immutability
    def write(self, vals):  # pylint: disable=method-required-super
        raise UserError(self.env._("Audit entries cannot be modified."))

    def unlink(self):  # pylint: disable=method-required-super,no-raise-unlink
        raise UserError(self.env._("Audit entries cannot be deleted."))
