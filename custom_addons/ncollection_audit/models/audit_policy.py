# -*- coding: utf-8 -*-
"""Audit Log 2.0: WHICH business models are recorded natively, and the capture itself.

WHY A SECOND CAPTURE PATH EXISTS (and why it is not "another tracking layer")
===========================================================================

OCA ``auditlog`` records by MONKEYPATCHING ``create`` / ``write`` / ``unlink`` onto the model class and reads every
stored field before and after inside a ``ThrowAwayCache``. On the models a business most needs audited that design was
MEASURED to break things (audit_rule.py ``_NC_WITHHELD``):

* ``res.partner``   - the ThrowAwayCache drops a queued recompute -> ``KeyError`` in ``_flush`` (partner create fails);
* ``res.users``     - the patched ``create`` replaces ncollection_core's override -> the plan seat limit is bypassed;
* ``account.move``  - nested patched writes recurse ~950 frames deep (limit 1000).

So those models were simply never audited, and the Audit Log was nearly empty (2 events on a working copy).

The native path writes into THE SAME tables (``auditlog.log`` / ``auditlog.log.line``, same value format), so the
history screen, the digest, the seals, retention and the platform's read-only channel all keep working unchanged.
It differs from OCA in exactly the three places that broke:

1. it is a normal ``_inherit = 'base'`` override, so it runs INSIDE every model's own ``super()`` chain - the seat
   limit and every other override still run, in order;
2. it reads only the fields the caller is writing, from the normal cache (no ThrowAwayCache, no full re-read);
3. it never re-enters itself: values are read, the business write runs once, values are read again.

Models audited by OCA rules are never captured here (no double rows). Which models are captured is DATA
(``ncollection.audit.policy``), seeded with safe defaults for the models actually installed, and editable only by the
Audit Log manager.

WHAT IS NEVER RECORDED
======================
Group-restricted fields (``groups=``: the ORM's own statement that a value is not for general reading), binary
fields, one2many lines (they are records of their own), framework columns, mail / activity plumbing, and every field
whose name says it is a credential (password, token, secret, key, signature, otp, hash, session).
"""
import logging
import re

from odoo import api, fields, models, tools
from odoo.exceptions import ValidationError
from odoo.orm.identifiers import NewId

from .audit_flags import capture_suppressed
from .auditlog_log import _company_of, _doc_type_of, _peek

_logger = logging.getLogger(__name__)

# The defaults: business documents and the configuration a trail is for. Only models present in the database are
# seeded; a missing app is not a gap.
DEFAULT_POLICY_MODELS = (
    'res.partner', 'res.partner.bank', 'res.company', 'res.users',
    'product.template', 'product.category',
    'account.move', 'account.payment', 'account.journal', 'account.account', 'account.tax',
    'account.payment.term', 'account.analytic.account', 'account.asset',
    'purchase.order', 'stock.picking', 'stock.warehouse', 'stock.location',
    'hr.employee',
)

SECRET_NAME = re.compile(r'(passw|token|secret|api_?key|signature|totp|otp_|_otp|_hash$|hash_|session|private|'
                         r'credential|salt)', re.I)
MAGIC = {'id', 'create_uid', 'create_date', 'write_uid', 'write_date', '__last_update', 'display_name'}
PLUMBING_PREFIX = ('message_', 'activity_', 'website_message', 'rating_', 'access_url', 'access_warning')
# Recompute churn and per-login bookkeeping (not something a person did).
NOISY = {
    'account.move': {'sequence_prefix', 'sequence_number', 'invoice_partner_display_name', 'commercial_partner_id',
                     'payment_state', 'amount_residual', 'amount_residual_signed', 'amount_total_in_currency_signed',
                     'made_sequence_gap', 'secure_sequence_number', 'inalterable_hash', 'needed_terms_dirty',
                     'tax_calculation_rounding_method', 'checked', 'is_move_sent', 'invoice_pdf_report_id'},
    'res.users': {'login_date', 'share', 'tz_offset', 'companies_count'},
    # customer_rank / supplier_rank are counters Odoo bumps when a document is posted for the partner: bookkeeping,
    # not a change a person made (and not something an Undo should ever put back).
    'res.partner': {'customer_rank', 'supplier_rank', 'commercial_company_name', 'complete_name', 'partner_share',
                    'calendar_last_notif_ack',
                    'tz_offset'},
}
MAX_TEXT = 4000           # bounded payload; a longer value is stored truncated and can never be restored
MAX_FIELDS = 200          # bounded snapshot per record
# NEVER natively audited, whatever a policy says: the trail itself (auditing it re-enters the capture without end),
# the framework, authentication and the platform layer (see audit_rule.py: same rule as for the OCA rules).
BLOCKED_PREFIX = ('auditlog.', 'ncollection.audit', 'ncollection.auth', 'ir.', 'bus.', 'base.', 'res.groups',
                  'ncollection.label.', 'ncollection.einvoice')


def blocked(env, name):
    """Why ``name`` may never get a native policy, or None."""
    from .audit_rule import SENSITIVE_MODELS
    if name.startswith(BLOCKED_PREFIX) or name in SENSITIVE_MODELS:
        return 'reserved'
    if name in env and env['auditlog.rule']._nc_platform_extended(env[name]):
        return 'platform'
    return None


class AuditPolicy(models.Model):
    _name = 'ncollection.audit.policy'
    _description = 'Audit Log policy: a business model recorded natively'
    _order = 'model_id'

    model_id = fields.Many2one('ir.model', required=True, ondelete='cascade', index=True, string='Document Type')
    model = fields.Char(related='model_id.model', store=True, string='Technical Name')
    active = fields.Boolean(default=True)
    log_create = fields.Boolean(string='Record creation', default=True)
    log_write = fields.Boolean(string='Record changes', default=True)
    log_unlink = fields.Boolean(string='Record deletion', default=True)
    excluded_field_ids = fields.Many2many(
        'ir.model.fields', 'ir_model_fields_ncollection_audit_policy_rel', 'ncollection_audit_policy_id',
        'ir_model_fields_id', string='Never record these fields',
        domain="[('model_id', '=', model_id)]",
        help="Added to the fields that are never recorded anyway (restricted, binary, credentials).")

    _model_unique = models.Constraint('unique(model_id)', 'A document type has one audit policy.')

    @api.constrains('model_id')
    def _check_model_allowed(self):
        for policy in self:
            if blocked(self.env, policy.model_id.model or ''):
                raise ValidationError(self.env._(
                    "%(model)s cannot be recorded by the Audit Log (the trail itself, the framework, authentication "
                    "and the platform layer never are).", model=policy.model_id.model))

    # ---- cache -----------------------------------------------------------

    @api.model
    @tools.ormcache()
    def _nc_policy_map(self):
        """{model: (create, write, unlink, frozenset(excluded))} for the active policies. Cached; any policy change
        clears it."""
        result = {}
        self.env.cr.execute("""
            SELECT p.model, p.log_create, p.log_write, p.log_unlink,
                   array_remove(array_agg(f.name), NULL)
              FROM ncollection_audit_policy p
         LEFT JOIN ir_model_fields_ncollection_audit_policy_rel r ON r.ncollection_audit_policy_id = p.id
         LEFT JOIN ir_model_fields f ON f.id = r.ir_model_fields_id
             WHERE p.active
          GROUP BY p.id, p.model, p.log_create, p.log_write, p.log_unlink""")
        for model, c, w, u, excluded in self.env.cr.fetchall():
            if model:
                result[model] = (bool(c), bool(w), bool(u), frozenset(excluded or ()))
        return result

    # The map is read with SQL, so a change is FLUSHED before the cache is cleared (else the next write reads the old
    # policy from the table and keeps recording a type that was just switched off).
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        self.flush_model()
        self.env.registry.clear_cache()
        return records

    def write(self, vals):
        result = super().write(vals)
        self.flush_model()
        self.env.registry.clear_cache()
        return result

    def unlink(self):
        result = super().unlink()
        self.flush_model()
        self.env.registry.clear_cache()
        return result

    # ---- seeding ---------------------------------------------------------

    @api.model
    def _nc_seed_policies(self):
        """Create the default policies that are missing. Idempotent (a second run creates nothing); never re-enables a
        policy an administrator switched off (it exists, so it is left alone); never a model OCA already audits."""
        IrModel = self.env['ir.model'].sudo()
        Rule = self.env['auditlog.rule'].sudo().with_context(active_test=False)
        oca = set(Rule.search([('model_id', '!=', False)]).mapped('model_id.model'))
        existing = set(self.sudo().with_context(active_test=False).search(
            [('model_id', '!=', False)]).mapped('model'))
        created = self.browse()
        for name in DEFAULT_POLICY_MODELS:
            if name in existing or name in oca or name not in self.env or blocked(self.env, name):
                continue
            model = IrModel.search([('model', '=', name)], limit=1)
            if not model or self.env[name]._transient or self.env[name]._abstract:
                continue
            with self.env.cr.savepoint():
                created |= self.sudo().create({'model_id': model.id})
        if created:
            _logger.info("ncollection_audit: seeded %s native audit polic(ies): %s",
                         len(created), ", ".join(created.mapped('model')))
        return created

    def _register_hook(self):
        result = super()._register_hook()
        try:
            self._nc_seed_policies()
        except Exception:                             # pragma: no cover - a registry load must finish
            _logger.exception("ncollection_audit: seeding native audit policies failed")
        return result


def _recordable_fields(model, excluded):
    """Field names of ``model`` the native capture may record (see the module docstring for what never is)."""
    names = []
    for name, field in model._fields.items():
        if (name in MAGIC or name in excluded or name in NOISY.get(model._name, ())
                or name.startswith(PLUMBING_PREFIX) or SECRET_NAME.search(name)):
            continue
        skip_types = ('binary', 'one2many', 'properties', 'properties_definition', 'json')
        if not field.store or field.groups or field.type in skip_types:
            continue
        names.append(name)
    return names


def _value(record, field):
    """The value in OCA auditlog's own format (``convert_to_read``), bounded."""
    value = field.convert_to_read(record[field.name], record)
    if isinstance(value, str) and len(value) > MAX_TEXT:
        value = value[:MAX_TEXT] + '…[truncated]'
    return value


def _text(env, field, value):
    """``*_value_text`` as OCA writes it: x2many as [(id, name)]."""
    if field.type in ('many2many',) and value:
        comodel = env[field.comodel_name].sudo()
        existing = comodel.browse(value).exists()
        return [(rec.id, rec.display_name) for rec in existing] + [
            (i, 'DELETED') for i in value if i not in existing.ids]
    return value


class Base(models.AbstractModel):
    _inherit = 'base'

    # ---- policy lookup ---------------------------------------------------

    def _ncal_policy(self, op):
        """(fields) when this model is natively audited for ``op`` here and now, else None. Cheap: one cached dict."""
        if (self._transient or self._abstract or not self.pool.ready
                or self._name.startswith(BLOCKED_PREFIX)
                or capture_suppressed()
                or 'ncollection.audit.policy' not in self.env):
            return None
        policy = self.env['ncollection.audit.policy']._nc_policy_map().get(self._name)
        if not policy or not policy[{'create': 0, 'write': 1, 'unlink': 2}[op]]:
            return None
        return _nc_fields_cached(self, policy[3])

    def _ncal_snapshot(self, names, truthy_only=False):
        values = {}
        # NO with_context here or below: OCA auditlog's ThrowAwayCache restores the cache only for environments that
        # existed before it; an environment created inside it (a new context) keeps pointing at the thrown-away cache
        # and corrupts the next flush ("Could not find all values of stock.picking to flush", measured).
        records = self.sudo()
        for record in records:
            row = {}
            for name in names:
                field = record._fields[name]
                # a creation / deletion snapshot never READS a computed field: reading it triggers the recompute, and
                # the pending result is lost when another module throws its cache away (OCA auditlog on sale.order
                # creating a picking: "Could not find all values of stock.picking to flush", measured)
                if truthy_only and field.compute and not field.inverse:
                    continue
                # and no snapshot ever computes: a computed / related value not already in the cache is skipped
                if (field.compute or field.related) and (
                        record.id not in field._get_cache(record.env) or record.env.is_to_compute(field, record)):
                    continue
                value = _value(record, field)
                if truthy_only and value in (False, None, '', [], 0, 0.0) and field.type not in ('boolean',):
                    continue
                if truthy_only and field.type == 'boolean' and not value:
                    continue
                row[name] = value
            values[record.id] = row
        return values

    def _ncal_write_logs(self, method, old, new):
        """Never lets the audit break the business operation: a failure loses this audit row, loudly."""
        try:
            self._ncal_write_logs_impl(method, old, new)
        except Exception:
            _logger.exception("ncollection_audit: could not record %s of %s %s", method, self._name, self.ids)

    def _ncal_write_logs_impl(self, method, old, new):
        """Create the audit rows (same tables and format as OCA auditlog)."""
        env = self.env
        Log = env['auditlog.log'].sudo()
        model_id = env['ir.model']._get_id(self._name)
        Fields = env['ir.model.fields'].sudo()
        request_id = env['auditlog.http.request'].sudo().current_http_request()
        session_id = env['auditlog.http.session'].sudo().current_http_session()
        vals_list = []
        for record in self.sudo():
            before, after = old.get(record.id, {}), new.get(record.id, {})
            names = sorted(set(before) | set(after))
            lines = []
            for name in names[:MAX_FIELDS]:
                o, n = before.get(name, False), after.get(name, False)
                if method == 'write' and o == n:
                    continue
                field_rec = Fields._get(self._name, name)
                if not field_rec:
                    continue
                field = record._fields[name]
                lines.append(fields.Command.create({
                    'field_id': field_rec.id,
                    'old_value': o if method != 'create' else False,
                    'old_value_text': _text(env, field, o) if method != 'create' else False,
                    'new_value': n if method != 'unlink' else False,
                    'new_value_text': _text(env, field, n) if method != 'unlink' else False,
                }))
            if method == 'write' and not lines:
                continue
            vals_list.append({
                'model_id': model_id, 'res_id': record.id, 'method': method, 'user_id': env.uid,
                'name': _name_of(record), 'log_type': 'full', 'line_ids': lines,
                'http_request_id': request_id, 'http_session_id': session_id, 'nc_source': 'native',
                'nc_company_id': _company_of(record), 'nc_doc_type': _doc_type_of(record),
            })
        if not vals_list:
            return
        # Isolated: a failure here (a value the column refuses, a broken hook) loses THIS audit row, loudly, and never
        # aborts the business write. The savepoint keeps the transaction usable after an SQL error.
        # flush=False: this can run INSIDE another module's own ThrowAwayCache (OCA auditlog on sale.order); a flush
        # there writes pending values whose cache that block then throws away (KeyError in _flush, measured).
        try:
            with env.cr.savepoint(flush=False):
                Log.create(vals_list)
        except Exception:
            _logger.exception("ncollection_audit: could not write the %s audit row(s) of %s %s", method, self._name,
                              self.ids)

    # ---- the three hooks --------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        names = records._ncal_policy('create') if records else None
        if names:
            try:
                snapshot = records._ncal_snapshot(names, truthy_only=True)
            except Exception:                         # an audit row must never abort the business write
                _logger.exception("ncollection_audit: could not snapshot %s on create", self._name)
            else:
                records._ncal_write_logs('create', {}, snapshot)
        return records

    def write(self, vals):
        names = self._ncal_policy('write') if self else None
        if not names:
            return super().write(vals)
        wanted = [name for name in vals if name in names]
        targets = self.filtered(lambda r: not isinstance(r.id, NewId))
        if not wanted or not targets:
            return super().write(vals)
        try:
            old = targets._ncal_snapshot(wanted)
        except Exception:
            _logger.exception("ncollection_audit: could not read old values of %s", self._name)
            old = None
        result = super().write(vals)
        if old is not None:
            try:
                new = targets._ncal_snapshot(wanted)
            except Exception:
                _logger.exception("ncollection_audit: could not read new values of %s", self._name)
            else:
                targets._ncal_write_logs('write', old, new)
        return result

    def unlink(self):
        names = self._ncal_policy('unlink') if self else None
        if names:
            # BEFORE the delete, like OCA: the pre-deletion values are what makes a restore possible, and a failed
            # delete rolls this row back with it.
            try:
                old = self.exists()._ncal_snapshot(names, truthy_only=True)
            except Exception:
                _logger.exception("ncollection_audit: could not snapshot %s before delete", self._name)
                old = {record.id: {} for record in self}
            self.exists()._ncal_write_logs('unlink', old, {})
        return super().unlink()


def _nc_fields_cached(model, excluded):
    cache = model.pool.__dict__.setdefault('_nc_audit_fields', {})
    key = (model._name, excluded)
    if key not in cache:
        cache[key] = frozenset(_recordable_fields(model, excluded))
    return cache[key]


def _name_of(record):
    """The record's name for the trail, without computing anything (see auditlog_log._peek)."""
    for name in (record._rec_name, 'name'):
        value = _peek(record, name) if name else False
        if value and isinstance(value, str):
            return value[:64]
    return '#%s' % record.id
