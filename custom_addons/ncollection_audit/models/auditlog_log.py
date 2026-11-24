# -*- coding: utf-8 -*-
"""P8-T05: the client IP, and a per-row content digest.

THE IP
======

The acceptance criterion names five fields:

    "changing an invoice amount produces an audit entry with old/new values,
     user, IP, and timestamp"

``auditlog`` supplies four of them. **There is no IP field anywhere in the
module** — not on ``auditlog.log``, not on ``auditlog.http.request``, not on
``auditlog.http.session``; the request model captures path, root URL, user and
context, and stops there. So the fifth field is ours.

``request.httprequest.remote_addr`` is the source, read exactly where and how
upstream already reads ``request.httprequest`` — behind an ``if not request``
guard, because a great many audited writes have no HTTP request at all. Measured
on this stack, not assumed:

    PROBE http request during shell write: False

Crons, queue jobs, ``odoo shell`` and the test suite all write with no request.
For those the IP is **empty, and that is the correct answer** — inventing
``127.0.0.1`` for a cron would be worse than a blank, because it would look like
a login from the server.

``proxy_mode`` is on in ``config/odoo.prod.conf`` (NOT in the dev
``config/odoo.conf`` — an earlier version of this docstring said otherwise and
was simply wrong), so werkzeug's ProxyFix has already
replaced ``remote_addr`` with the real client address from the nginx edge rather
than the proxy's own. Without that flag this field would record the reverse
proxy on every single row.

**A limitation worth stating rather than discovering.** When the platform writes
into a tenant database over RPC on a user's behalf (``ncollection_saas`` config
sync, provisioning), ``remote_addr`` is the *platform server's* address, not the
end user's. The row is still honest — that write genuinely came from the
platform — but it is not the human's IP, and a reader who assumes otherwise
would be misled.

THE DIGEST
==========

``auditlog`` has no tamper evidence of any kind (grepped for
hash/checksum/chain/tamper: zero hits), and its own manager group holds
``perm_unlink=1`` on ``auditlog.log``. An audit trail that a compromised admin
can silently edit is a record of what the attacker wanted recorded.

Each row gets a digest over its own content. Chaining across rows is done
separately and cheaply by ``ncollection.audit.seal`` — deliberately NOT here:
a chain computed at insert time has to read the previous row, and ``auditlog``
already pays two full reads plus one insert per changed field on every audited
write. Adding a locked read to that hot path is the wrong trade, and an
unlocked one forks under concurrency and then reports the fork as tampering.

**The digest is computed by reading the CREATED RECORD, not the incoming vals**,
through ``_nc_row_digest`` — the single function the verifier also calls. That
costs one extra UPDATE per log row, and buys the only property that makes a
tamper check worth having: writer and verifier cannot disagree, because they are
the same code.
"""
import hashlib
import json
import logging

from odoo import api, fields, models

from .audit_flags import current_undo_id

try:
    from odoo.http import request
except ImportError:                                   # pragma: no cover
    request = None

# Fields that identify WHAT happened. `create_date` is included: a row whose
# timestamp is moved is tampered with even if nothing else changed.
_logger = logging.getLogger(__name__)

_DIGEST_HEAD_FIELDS = (
    'model_model', 'res_id', 'res_ids', 'method', 'log_type', 'name',
)


ACTIONS = {'create': 'create', 'write': 'update', 'unlink': 'delete'}


def _peek(record, name, default=False):
    """``record[name]`` ONLY when that needs no computation: a computed or related value that is not already in the
    cache is not computed here. The capture can run while another module holds a throw-away cache (OCA auditlog
    creating a sale order creates its picking): a value computed now would be lost with that cache and the next
    flush would fail. Missing scope is acceptable; a broken business write is not."""
    field = record._fields.get(name)
    if field is None:
        return default
    if field.compute or field.related:
        env = record.env
        if record.id not in field._get_cache(env) or env.is_to_compute(field, record):
            return default
    return record[name]


def _company_of(record):
    if record._name == 'res.company':
        return record.id
    field = record._fields.get('company_id')
    if field is not None and field.type == 'many2one' and field.comodel_name == 'res.company':
        company = _peek(record, 'company_id')
        return company.id if company else False
    # several companies (e.g. account.account.company_ids): the event belongs to one only when the record does
    many = record._fields.get('company_ids')
    if many is not None and many.type == 'many2many' and many.comodel_name == 'res.company':
        companies = _peek(record, 'company_ids') or record.env['res.company']
        return companies.id if len(companies) == 1 else False
    return False


def _doc_type_of(record):
    """A finer document type than the model where one model holds several (invoice / bill / credit note ...)."""
    name = record._name
    try:
        if name == 'account.move':
            return 'account.move:%s' % (_peek(record, 'move_type') or 'entry')
        if name == 'account.payment':
            return 'account.payment:%s' % (_peek(record, 'payment_type') or 'inbound')
        if name == 'res.partner':
            fields_ = record._fields
            if 'supplier_rank' in fields_ and _peek(record, 'supplier_rank') and not _peek(record, 'customer_rank'):
                return 'res.partner:supplier'
            if 'customer_rank' in fields_ and _peek(record, 'customer_rank'):
                return 'res.partner:customer'
            return 'res.partner:contact'
        if name == 'stock.picking' and 'picking_type_code' in record._fields:
            return 'stock.picking:%s' % (_peek(record, 'picking_type_code') or 'internal')
    except Exception:                                       # pragma: no cover
        return name
    return name


class AuditlogLog(models.Model):
    _inherit = 'auditlog.log'

    nc_remote_addr = fields.Char(
        string='Client IP', readonly=True, index=True,
        help="Client address of the request that caused this entry. Empty for "
             "writes with no HTTP request — crons, queue jobs and scheduled "
             "work legitimately have none.")
    # ---- Audit Log 2.0 (company scope, kind, document type, undo link) ----
    nc_company_id = fields.Many2one(
        'res.company', string='Company', readonly=True, index=True, ondelete='set null',
        help="The company of the recorded document when the event was written. Empty for a document with no "
             "company (shared master data) and for older events whose document no longer exists.")
    nc_action = fields.Selection(
        [('create', 'Create'), ('update', 'Update'), ('delete', 'Delete'), ('other', 'Other')],
        string='Action', readonly=True, index=True)
    nc_doc_type = fields.Char(string='Document Type Key', readonly=True, index=True)
    nc_source = fields.Selection([('oca', 'Rule'), ('native', 'Policy')], string='Recorded By', readonly=True,
                                 default='oca')
    # the document whose history this event belongs to (an invoice line's events -> its invoice)
    nc_parent_model = fields.Char(string='Parent Document Model', readonly=True)
    nc_parent_res_id = fields.Integer(string='Parent Document ID', readonly=True)
    nc_undo_id = fields.Many2one('ncollection.audit.undo', string='Written by Undo', readonly=True, index=True,
                                 ondelete='restrict')
    nc_content_hash = fields.Char(
        string='Content Digest', readonly=True, index=True,
        help="SHA-256 over this entry's own content. A mismatch means the row "
             "was altered after it was written.")

    # ---- capture ---------------------------------------------------------

    @api.model
    def _nc_remote_addr(self):
        """The client address, or empty when there is no request.

        Guarded the way upstream guards it (`auditlog_http_request.py` does
        `if not request: return False` before touching `request.httprequest`) —
        `request` is a werkzeug local that is falsy outside an HTTP call, and
        touching `.httprequest` on it raises.
        """
        if not request:
            return ''
        try:
            return request.httprequest.remote_addr or ''
        except (AttributeError, RuntimeError):
            # A request object without a live werkzeug request underneath it.
            # An audit row must never fail to be written because its metadata
            # could not be read — the row is the point, the IP is a detail.
            return ''

    @api.model_create_multi
    def create(self, vals_list):
        remote_addr = self._nc_remote_addr()
        undo = current_undo_id()
        for vals in vals_list:
            vals.setdefault('nc_remote_addr', remote_addr)
            vals.setdefault('nc_action', ACTIONS.get(vals.get('method'), 'other'))
            if undo:
                vals.setdefault('nc_undo_id', undo)
            if vals.get('res_id') and vals.get('model_id') and (
                    'nc_company_id' not in vals or 'nc_doc_type' not in vals):
                company, doc_type = self._nc_scope_of(vals['model_id'], vals['res_id'])
                vals.setdefault('nc_company_id', company)
                vals.setdefault('nc_doc_type', doc_type)
        records = super().create(vals_list)
        # Digest AFTER creation, from the record. See the module docstring:
        # symmetry with the verifier is worth one UPDATE.
        for record in records:
            try:
                record.nc_content_hash = record._nc_row_digest()
            except Exception:
                # GUARDED for the same reason _nc_remote_addr is, one field
                # above: `auditlog.rule` calls `auditlog.log.create()` from
                # inside the patched write with no try/except of its own, so an
                # exception here does not merely lose a digest — it aborts the
                # AUDITED BUSINESS WRITE. Saving an invoice must not fail
                # because its audit row could not be hashed. A blank digest is
                # already treated as untrustworthy by _nc_verify_content, so
                # the failure stays visible instead of silent.
                _logger.exception(
                    "ncollection_audit: could not digest audit log %s; the row "
                    "is kept and will verify as untrusted", record.id)
        # WRITTEN NOW, not left in the cache: a module install loads data with cache invalidations between batches,
        # and an unflushed digest was dropped there (only the first row of a batch kept it).
        records.flush_recordset(['nc_content_hash'])
        return records

    def init(self):
        # The two access paths of the history screen: newest first (id breaks ties), and one document's timeline.
        # IF NOT EXISTS: init runs on every install / upgrade.
        self.env.cr.execute("""
            CREATE INDEX IF NOT EXISTS auditlog_log_nc_date_idx ON auditlog_log (create_date DESC, id DESC);
            CREATE INDEX IF NOT EXISTS auditlog_log_nc_record_idx
                ON auditlog_log (model_model, res_id, create_date, id);
            CREATE INDEX IF NOT EXISTS auditlog_log_nc_model_idx ON auditlog_log (model_id);
            CREATE INDEX IF NOT EXISTS auditlog_log_nc_parent_idx ON auditlog_log (nc_parent_model, nc_parent_res_id)
                WHERE nc_parent_model IS NOT NULL;
        """)

    @api.model
    def _nc_scope_of(self, model_id, res_id):
        """(company id, document type key) of the recorded document, read as superuser at write time. Never raises:
        an audit row must not fail because its scope could not be read (the row then has no company and stays
        governed by the model-level checks)."""
        try:
            model = self.env['ir.model'].sudo().browse(model_id).model
            if not model or model not in self.env:
                return False, model or False
            record = self.env[model].sudo().browse(res_id)
            if not record.exists():
                return False, model
            return _company_of(record), _doc_type_of(record)
        except Exception:                                   # pragma: no cover - defensive, see docstring
            _logger.exception("ncollection_audit: could not read the scope of %s,%s", model_id, res_id)
            return False, False

    # ---- the digest ------------------------------------------------------

    def _nc_row_digest(self):
        """SHA-256 over this row's content, as data rather than as text.

        `json.dumps(..., sort_keys=True)` rather than string concatenation: a
        concatenated digest can be forged by moving a delimiter between two
        adjacent fields, which is a real and well-known way to make a hash
        agree about two different records.
        """
        self.ensure_one()
        payload = {
            'head': {name: self[name] or '' for name in _DIGEST_HEAD_FIELDS},
            'user_id': self.user_id.id or 0,
            'create_date': fields.Datetime.to_string(self.create_date) or '',
            'remote_addr': self.nc_remote_addr or '',
            'lines': sorted(
                [line.field_name or '', line.old_value or '',
                 line.new_value or '']
                for line in self.line_ids),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode('utf-8')
        ).hexdigest()

    def _nc_verify_content(self):
        """``(ok_records, tampered_records)`` by re-deriving each digest.

        A row with no digest at all counts as **tampered**, not as fine. Rows
        predating this module are the obvious case, and treating "no evidence"
        as "no problem" is how a tamper check becomes decoration — the caller
        is told, and decides.
        """
        ok = self.browse()
        tampered = self.browse()
        for record in self:
            if (record.nc_content_hash
                    and record.nc_content_hash == record._nc_row_digest()):
                ok |= record
            else:
                tampered |= record
        return ok, tampered
