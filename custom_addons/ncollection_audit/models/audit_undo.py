# -*- coding: utf-8 -*-
"""Audit Log 2.0: controlled Undo.

WHAT UNDO IS, AND WHAT IT IS NOT
================================
Undo is an ordinary ORM operation, run AS THE USER, that puts back what one recorded event changed:

* Update -> write the recorded OLD values of the chosen fields back (only fields nobody changed since);
* Create -> delete the document (or archive it, when other documents already point at it);
* Delete -> create a NEW document from the recorded pre-deletion values (a new id; links other documents had to
  the original are NOT recreated, and the screen says so).

It is never a way around the ERP. Every write goes through the model's own ``write`` / ``create`` / ``unlink`` as the
user, so the ACLs, record rules, company checks, constraints, settings delegation and accounting protections all
apply exactly as they do on the form. On top of that it REFUSES, with the reason and the correct workflow:

* posted / reconciled / otherwise finalised accounting (journal entries, payments, statements, reconciliations,
  taxes in use, internal transfers, e-invoices, cheque prints, the invoice audit itself) - those are corrected by a
  credit note, a reversal, a reset to draft or an unreconcile, never by rewriting;
* any document in a finalised state (posted, done, paid, approved, locked, cancelled, confirmed sale / purchase);
* anything dated on or before the company's fiscal or hard lock date;
* access rights and roles (changed from Settings, which checks and records them);
* values that were truncated, restricted fields, credentials, computed / system-set fields.

Every Undo is recorded twice: in this append-only ledger (``ncollection.audit.undo``: who, when, which event, what was
put back, the result) and as ordinary audit events written by the operation itself, linked to it. The original event
is never edited. ``unique(original_log_ref)`` makes a second Undo of the same event impossible, also under
concurrency; the event row and the document row are locked (NOWAIT) while it runs, and the preview's fingerprint must
still match at apply time, so nothing is applied that the user did not see.
"""
import hashlib
import json
import logging

import psycopg2

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import SQL, mute_logger

from . import audit_values as V
from .audit_flags import suppress_capture, undo_scope
from .audit_policy import WHITELIST

_logger = logging.getLogger(__name__)

PROTECTED = {
    'account.partial.reconcile', 'account.full.reconcile', 'account.bank.statement',
    'account.bank.statement.line', 'account.tax', 'account.tax.repartition.line', 'account.asset',
    'account.asset.line', 'account.analytic.line', 'ncollection.internal.transfer', 'ncollection.invoice.audit',
    'ncollection.invoice.adjustment', 'ncollection.cheque.print.job', 'ncollection.cheque.print.event',
    'ncollection.journal.transaction', 'ncollection.bank.payment.batch', 'res.users', 'res.groups',
    'ncollection.role', 'res.currency.rate',
}
PROTECTED_PREFIX = ('ncollection.einvoice', 'ncollection.audit', 'auditlog.', 'ir.', 'ncollection.auth')
# Accounting documents: field Undo only while draft; Create-Undo only if never posted; Delete-Undo never.
DRAFT_ONLY = {'account.move', 'account.payment'}
FINAL_STATES = {'posted', 'done', 'paid', 'reconciled', 'cancel', 'cancelled', 'sale', 'purchase', 'approved',
                'locked', 'in_payment', 'validated', 'closed',
                # Odoo 19 account.payment (posted / cancelled / rejected) and a superseded budget
                'in_process', 'canceled', 'rejected', 'revised'}
# Fields Undo never restores: they reopen a period, change the books' frame, or reclassify posted history. They are
# changed only through their own configuration screens (Lock Dates with a lock exception, the chart, the journal).
NEVER_RESTORE = {
    'res.company': {'fiscalyear_lock_date', 'hard_lock_date', 'tax_lock_date', 'sale_lock_date',
                    'purchase_lock_date', 'account_fiscal_country_id', 'currency_id', 'chart_template',
                    'account_opening_date', 'fiscalyear_last_day', 'fiscalyear_last_month'},
    'account.account': {'account_type', 'reconcile', 'currency_id', 'code', 'company_ids'},
    'account.journal': {'type', 'default_account_id', 'currency_id', 'code', 'suspense_account_id'},
    'product.category': {'property_cost_method', 'property_valuation'},
}
# A created master record that posted entries reference is never archived by Undo (that hides balances).
ACCOUNTING_MASTER = {'res.partner', 'account.account', 'account.journal', 'product.template',
                     'product.product', 'account.tax', 'account.analytic.account'}
DATE_FIELDS = ('date', 'invoice_date', 'accounting_date')
# Restoring a line's product or unit makes Odoo recompute the line's price, taxes, label and unit from the product
# (stored computes that depend on it). The preview shows only what is restored, so these are written back with their
# CURRENT values unless they are restored too: nothing changes that the user did not see (accounting review HIGH).
KEEP_WITH = {'account.move.line': ('product_id', 'product_uom_id')}
KEPT_FIELDS = {'account.move.line': ('price_unit', 'tax_ids', 'name', 'product_uom_id', 'discount', 'account_id',
                                     'analytic_distribution')}


def _with_kept_values(record, vals):
    if not set(KEEP_WITH.get(record._name, ())) & set(vals):
        return vals
    vals = dict(vals)
    for name in KEPT_FIELDS[record._name]:
        if name in vals:
            continue
        field = record._fields[name]
        value = record[name]
        vals[name] = [(6, 0, value.ids)] if field.type == 'many2many' else (
            value.id if field.type == 'many2one' else value)
    return vals


def _hint(env, model):
    hints = {
        'account.move': env._("Use the document's own Reset to Draft, Credit Note or Reverse Entry."),
        'account.move.line': env._("Edit the line on the draft invoice; a posted invoice is corrected with a credit "
                                   "note."),
        'account.payment': env._("Use the payment's own Reset to Draft / Cancel, and Unreconcile it in Accounting."),
        'account.partial.reconcile': env._("Use Unreconcile in Bank Transactions & Matching."),
        'account.full.reconcile': env._("Use Unreconcile in Bank Transactions & Matching."),
        'account.bank.statement.line': env._("Use Bank Transactions & Matching (Undo / Unreconcile)."),
        'account.bank.statement': env._("Use Bank Transactions & Matching."),
        'account.tax': env._("Change taxes from Accounting > Configuration; a tax used on documents is not "
                             "rewritten."),
        'ncollection.internal.transfer': env._("Reverse it with a new Internal Transfer; a posted transfer is never "
                                               "rewritten."),
        'res.users': env._("Change users and their access from Settings > Users, which checks and records it."),
        'res.groups': env._("Change access from Settings > Roles & Permissions."),
        'ncollection.role': env._("Change roles from Settings > Roles & Permissions."),
    }
    return hints.get(model) or env._("Use the document's own workflow (cancel, reset, correction).")


class AuditUndo(models.Model):
    _name = 'ncollection.audit.undo'
    _description = 'Audit Log Undo (append-only)'
    _order = 'id desc'

    original_log_id = fields.Many2one('auditlog.log', string='Event Undone', readonly=True, index=True,
                                      ondelete='set null')
    # The event id, kept when retention later prunes the event itself (the link above then empties): the one-Undo-
    # per-event guard and the ledger must outlive the trail's retention, which must never be blocked by an Undo.
    original_log_ref = fields.Integer(string='Event Undone (id)', readonly=True, required=True, index=True)
    kind = fields.Selection([('update', 'Restore values'), ('create', 'Remove created document'),
                             ('delete', 'Restore deleted document'), ('version', 'Restore a version')],
                            required=True, readonly=True)
    mode = fields.Selection([('restore', 'Values restored'), ('delete', 'Deleted'), ('archive', 'Archived'),
                             ('recreate', 'Recreated as a new document'), ('version', 'Version restored')],
                            readonly=True)
    user_id = fields.Many2one('res.users', string='Undone By', required=True, readonly=True,
                              default=lambda self: self.env.user, ondelete='restrict')
    company_id = fields.Many2one('res.company', readonly=True, index=True)
    model = fields.Char(readonly=True, required=True)
    res_id = fields.Integer(string='Original Record', readonly=True)
    result_res_id = fields.Integer(string='Resulting Record', readonly=True)
    field_names = fields.Char(readonly=True)
    details = fields.Text(readonly=True, help="JSON: field -> [value before the Undo, value put back].")
    state = fields.Selection([('pending', 'Running'), ('done', 'Done')], default='pending', readonly=True)
    result_log_ids = fields.One2many('auditlog.log', 'nc_undo_id', string='Events Written by the Undo',
                                     readonly=True)

    # one Undo per event; restoring a VERSION may be done again later (the document can change again in between)
    _original_unique_idx = models.UniqueIndex("(original_log_ref) WHERE kind != 'version'",
                                              'This event has already been undone.')

    def write(self, vals):
        # Append-only: the only write is the engine's own pending -> done (sudo, inside the same transaction).
        if not self.env.su or set(vals) - {'state', 'mode', 'result_res_id', 'details'}:
            raise AccessError(self.env._("Undo records cannot be changed."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_never(self):
        raise AccessError(self.env._("Undo records cannot be deleted."))

    # =====================================================================
    # analysis
    # =====================================================================

    @api.model
    def _nc_refuse(self, reason, hint=None):
        return {'available': False, 'reason': reason, 'hint': hint or '', 'rows': [], 'modes': []}

    @api.model
    def _nc_target(self, log):
        """The model (or None when its app is gone) and the record (possibly not existing)."""
        if not log.model_model or log.model_model not in self.env:
            return None, None
        return self.env[log.model_model], self.env[log.model_model].with_context(active_test=False).browse(
            log.res_id)

    @api.model
    def _nc_locked(self, record):
        """A reason the document is finalised, or None."""
        env = self.env
        if 'state' in record._fields and record.state in FINAL_STATES:
            label = dict(record._fields['state']._description_selection(env)).get(record.state, record.state)
            return env._("The document is %(state)s.", state=label)
        company = record.company_id if 'company_id' in record._fields else env.company
        if record._name in DRAFT_ONLY:
            sudo = record.sudo()
            if 'state' in sudo._fields and sudo.state != 'draft':
                return env._("Only a draft accounting document is restored by Undo.")
            if 'move_id' in sudo._fields and sudo.move_id and sudo.move_id.state != 'draft':
                return env._("Its journal entry is not a draft.")
        if record._name.startswith('account.') or record._name in DRAFT_ONLY:
            # every lock Odoo knows (fiscal, hard, tax, sales, purchases): the user's own exceptions are not applied
            # here on purpose - a lock exception is granted for the document's own workflow, not for a blind restore
            locks = [d for d in (getattr(company, name, False) for name in (
                'fiscalyear_lock_date', 'hard_lock_date', 'tax_lock_date', 'sale_lock_date',
                'purchase_lock_date')) if d]
            if locks:
                lock = max(locks)
                for name in DATE_FIELDS:
                    if name in record._fields and record[name] and fields.Date.to_date(record[name]) <= lock:
                        return env._("It is dated in a locked period (locked up to %(date)s).",
                                     date=fields.Date.to_string(lock))
        return None

    @api.model
    def _nc_policy_refusal(self, log, model):
        env = self.env
        if model is None:
            return env._("This document type is no longer installed.")
        name = model._name
        if name in PROTECTED or name.startswith(PROTECTED_PREFIX):
            return env._("Undo is not available for this document type.")
        if name in DRAFT_ONLY and log.nc_action == 'delete':
            return env._("A deleted accounting document is never recreated from the trail.")
        if name == 'account.move.line':
            # Only invoice product lines are recorded (with their invoice as parent); a journal item of any other kind
            # is never touched, and a deleted line is never re-added: taxes, accounts and totals are the invoice's
            # own computation, so a line is added again on the draft invoice itself.
            if log.nc_parent_model != 'account.move':
                return env._("Undo is not available for this document type.")
            if log.nc_action == 'delete':
                return env._("A deleted invoice line is never re-added from the trail: add it again on the draft "
                             "invoice.")
        return None

    @api.model
    def _nc_later(self, log):
        """Recorded events on the same document AFTER this one (as superuser: a conflict is a fact, even when the
        later change was made in a company this user cannot see; only its existence is reported)."""
        return self.env['auditlog.log'].sudo().search([
            ('model_model', '=', log.model_model), ('res_id', '=', log.res_id),
            '|', ('create_date', '>', log.create_date),
            '&', ('create_date', '=', log.create_date), ('id', '>', log.id)])

    @api.model
    def _nc_field_problem(self, model, field, line):
        env = self.env
        if field is None:
            return env._("This field no longer exists.")
        if field.name in NEVER_RESTORE.get(model._name, ()):
            return env._("Changed only from its configuration screen (it would reopen a period or reclassify posted "
                         "entries).")
        if getattr(field, 'company_dependent', False):
            return env._("A per-company setting: change it from the document in the right company.")
        if V.masked(env, model._name, field):
            return env._("Restricted field: it is never restored from the trail.")
        if not field.store or field.type not in V.SUPPORTED:
            return env._("This kind of field cannot be restored.")
        # an editable computed value is restorable only on whitelisted models (invoice lines), never generally
        if field.compute and not field.inverse and (field.readonly or model._name not in WHITELIST):
            return env._("Computed by the system.")
        if field.readonly and not (field.compute and field.inverse):
            return env._("Set by the system (read-only).")
        if V.is_truncated(line.old_value):
            return env._("The recorded value was too long and was stored shortened.")
        return None

    @api.model
    def _nc_target_exists(self, field, value):
        if field.type == 'many2one' and value:
            target = self.env[field.comodel_name].with_context(active_test=False).browse(value)
            if not target.sudo().exists():
                return False, self.env._("The linked record no longer exists.")
            if not target.has_access('read'):
                return False, self.env._("You cannot access the linked record.")
        if field.type == 'many2many' and value:
            existing = self.env[field.comodel_name].sudo().with_context(active_test=False).browse(value).exists()
            if len(existing) != len(value):
                return False, self.env._("Some linked records no longer exist.")
            if not self.env[field.comodel_name].browse(existing.ids).has_access('read'):
                return False, self.env._("You cannot access the linked record.")
        return True, None

    @api.model
    def _nc_analyse(self, log, selected=None):
        """The preview: what Undo would do now, or why it cannot. Pure read (dependency probes roll back)."""
        env = self.env
        if not env.user.has_group('ncollection_audit.group_audit_undo'):
            return self._nc_refuse(env._("You are not allowed to undo audited changes."))
        if self.sudo().search_count([('original_log_ref', '=', log.id), ('kind', '!=', 'version')]):
            return self._nc_refuse(env._("This event has already been undone."))
        model, record = self._nc_target(log)
        refusal = self._nc_policy_refusal(log, model)
        if refusal:
            return self._nc_refuse(refusal, _hint(env, log.model_model) if model is not None else '')
        exists = bool(record.sudo().exists())
        if log.nc_action in ('update', 'create'):
            if not exists:
                return self._nc_refuse(env._("The document no longer exists."))
            if not record.has_access('write' if log.nc_action == 'update' else 'unlink'):
                return self._nc_refuse(env._("You are not allowed to change this document."))
            # an invoice line follows its invoice: only while the invoice is a draft and not in a locked period
            locked = self._nc_locked(record.sudo().move_id if model._name == 'account.move.line' else record)
            if locked:
                return self._nc_refuse(locked, _hint(env, model._name))
        currency = V.currency_of(record.sudo()) if exists else env.company.currency_id
        if log.nc_action == 'update':
            return self._nc_analyse_update(log, model, record, currency, selected)
        if log.nc_action == 'create':
            return self._nc_analyse_create(log, model, record)
        if log.nc_action == 'delete':
            if exists:
                return self._nc_refuse(env._("The document still exists."))
            return self._nc_analyse_delete(log, model, currency)
        return self._nc_refuse(env._("Only Create, Update and Delete events can be undone."))

    @api.model
    def _nc_analyse_update(self, log, model, record, currency, selected):
        env = self.env
        later = {}
        for other in self._nc_later(log):
            for line in other.line_ids:
                later.setdefault(line.field_name, other.id)
        rows, vals = [], {}
        for line in log.line_ids:
            field = model._fields.get(line.field_name)
            label = V.label(env, model._name, line.field_name, line.field_description)
            row = {'field': line.field_name, 'label': label, 'restorable': False, 'conflict': False, 'reason': ''}
            problem = self._nc_field_problem(model, field, line)
            if problem:
                row['reason'] = problem
                rows.append(row)
                continue
            row['current'], _k = V.display_current(env, record.sudo(), field, currency)
            row['restore'], _k = V.display(env, model._name, field, line.old_value, line.old_value_text, currency)
            try:
                recorded_new, restore = V.parse(field, line.new_value), V.parse(field, line.old_value)
            except (ValueError, SyntaxError):
                row['reason'] = env._("The recorded value cannot be read back.")
                rows.append(row)
                continue
            now = V.current(record.sudo(), field)
            if line.field_name in later:
                row.update(conflict=True,
                           reason=env._("Changed again later: restoring would overwrite that change."))
            elif not V.same(field, now, recorded_new):
                row.update(conflict=True, reason=env._("The current value is not the one this event wrote."))
            else:
                ok, why = self._nc_target_exists(field, restore)
                if not ok:
                    row['reason'] = why
                else:
                    row['restorable'] = True
                    if selected is None or line.field_name in selected:
                        vals[line.field_name] = restore if field.type != 'many2many' else [(6, 0, restore)]
                        row['selected'] = True
            rows.append(row)
        if not any(r['restorable'] for r in rows):
            reason = env._("No field of this change can be restored safely.")
            result = self._nc_refuse(reason)
            result['rows'] = rows
            return result
        related = []
        if model._name in KEEP_WITH and set(KEEP_WITH[model._name]) & {r['field'] for r in rows if r['restorable']}:
            related.append(env._("Restoring the product or the unit keeps the line's current price, taxes, account, "
                                 "analytic split, description and discount (the system would otherwise re-price the "
                                 "line); restore those "
                                 "too if you want their earlier values."))
        return {'available': True, 'kind': 'update', 'modes': ['restore'], 'rows': rows, 'vals': vals,
                'reason': '', 'hint': '', 'related': related,
                'effect': env._("The selected fields get their earlier values back. Nothing else changes.")}

    @api.model
    def _nc_probe(self, operation):
        """Run ``operation`` in a savepoint and roll it back: (ok, error message)."""
        class _Rollback(Exception):
            pass
        try:
            with self.env.cr.savepoint(), suppress_capture():
                operation()
                raise _Rollback()
        except _Rollback:
            return True, None
        except (UserError, ValidationError, AccessError) as error:
            return False, str(error.args[0] if error.args else error)
        except psycopg2.Error as error:
            return False, (error.diag.message_primary if getattr(error, 'diag', None) else str(error))
        finally:
            self.env.invalidate_all()

    @api.model
    def _nc_analyse_create(self, log, model, record):
        env = self.env
        later = self._nc_later(log)
        if later:
            return self._nc_refuse(env._("The document was changed after it was created; undo those changes "
                                         "first, or remove it through its own workflow."))
        if model._name in DRAFT_ONLY:
            sudo = record.sudo()
            if ('posted_before' in sudo._fields and sudo.posted_before) or (
                    'state' in sudo._fields and sudo.state != 'draft'):
                return self._nc_refuse(env._("This accounting document has been posted."), _hint(env, model._name))
        ok, error = self._nc_probe(lambda: record.unlink())
        if ok:
            return {'available': True, 'kind': 'create', 'modes': ['delete'], 'rows': [], 'related': [],
                    'reason': '', 'hint': '',
                    'effect': env._("The document is deleted. The deletion itself is recorded and can be undone.")}
        if ('active' in model._fields and record.sudo().active and record.has_access('write')
                and model._name not in ACCOUNTING_MASTER):
            return {'available': True, 'kind': 'create', 'modes': ['archive'], 'rows': [], 'related': [error],
                    'reason': '', 'hint': '',
                    'effect': env._("It cannot be deleted (%(reason)s), so it is archived instead: hidden from lists, "
                                    "kept with its links.", reason=error)}
        return self._nc_refuse(env._("It cannot be removed: %(reason)s", reason=error))

    @api.model
    def _nc_analyse_delete(self, log, model, currency):
        env = self.env
        if not log.line_ids:
            return self._nc_refuse(env._("The values before deletion were not recorded for this event, so the "
                                         "document cannot be rebuilt."))
        if not model.has_access('create'):
            return self._nc_refuse(env._("You are not allowed to create this kind of document."))
        rows, vals, related = [], {}, []
        for line in log.line_ids:
            field = model._fields.get(line.field_name)
            label = V.label(env, model._name, line.field_name, line.field_description)
            row = {'field': line.field_name, 'label': label, 'restorable': False, 'conflict': False, 'reason': '',
                   'current': '', 'selected': False}
            problem = self._nc_field_problem(model, field, line)
            if problem:
                row['reason'] = problem
                rows.append(row)
                continue
            row['restore'], _k = V.display(env, model._name, field, line.old_value, line.old_value_text, currency)
            try:
                value = V.parse(field, line.old_value)
            except (ValueError, SyntaxError):
                row['reason'] = env._("The recorded value cannot be read back.")
                rows.append(row)
                continue
            ok, why = self._nc_target_exists(field, value)
            if not ok:
                row['reason'] = why
                if field.required:
                    return dict(self._nc_refuse(env._("A required link is missing: %(field)s (%(why)s).",
                                                      field=label, why=why)), rows=rows)
                related.append(env._("%(field)s is left empty: %(why)s", field=label, why=why))
                rows.append(row)
                continue
            row.update(restorable=True, selected=True)
            vals[line.field_name] = value if field.type != 'many2many' else [(6, 0, value)]
            rows.append(row)
        ok, error = self._nc_probe(lambda: model.create(dict(vals)))
        if not ok:
            return dict(self._nc_refuse(env._("It cannot be rebuilt: %(reason)s", reason=error)), rows=rows)
        related.append(env._("It is created as a NEW document with a new number/id. Links other documents had to the "
                             "deleted one (#%(id)s) are not recreated.", id=log.res_id))
        return {'available': True, 'kind': 'delete', 'modes': ['recreate'], 'rows': rows, 'vals': vals,
                'related': related, 'reason': '', 'hint': '',
                'effect': env._("A new document is created from the values recorded before the deletion.")}

    @api.model
    def _nc_fingerprint(self, log, analysis):
        payload = {'log': log.id, 'kind': analysis.get('kind'), 'modes': analysis.get('modes'),
                   'rows': [(r['field'], r.get('current'), r.get('restore'), r.get('restorable'), r.get('conflict'))
                            for r in analysis.get('rows', [])]}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()

    # =====================================================================
    # the public calls (from the Audit Log screen, as the user)
    # =====================================================================

    @api.model
    def nc_undo_preview(self, log_id, field_names=None):
        if field_names is not None and (not isinstance(field_names, list) or not all(
                isinstance(name, str) for name in field_names)):
            raise UserError(self.env._("Malformed request."))
        log = self.env['ncollection.audit.service']._nc_log(log_id)
        selected = set(field_names) if isinstance(field_names, list) else None
        analysis = self._nc_analyse(log, selected)
        analysis.pop('vals', None)
        analysis['fingerprint'] = self._nc_fingerprint(log, analysis) if analysis.get('available') else ''
        return analysis

    @api.model
    def nc_undo_apply(self, log_id, field_names, fingerprint, mode=None):
        env = self.env
        log = env['ncollection.audit.service']._nc_log(log_id)
        if not isinstance(field_names, list) or not isinstance(fingerprint, str) or not all(
                isinstance(name, str) for name in field_names):
            raise UserError(env._("Malformed request."))
        cr = env.cr
        try:
            # muted: a busy document is an expected answer here, not a server error worth an ERROR line
            with mute_logger('odoo.sql_db'):
                cr.execute(SQL("SELECT id FROM auditlog_log WHERE id = %s FOR UPDATE NOWAIT", log.id))
            model, record = self._nc_target(log)
            if model is not None and log.nc_action in ('update', 'create') and record.sudo().exists():
                with mute_logger('odoo.sql_db'):
                    cr.execute(SQL("SELECT id FROM %s WHERE id = %s FOR UPDATE NOWAIT", SQL.identifier(model._table),
                               record.id))
        except psycopg2.errors.LockNotAvailable:
            raise UserError(env._("This document is being changed by someone else right now. Try again.")) from None
        analysis = self._nc_analyse(log, set(field_names) if log.nc_action == 'update' else None)
        if not analysis.get('available'):
            raise UserError(analysis.get('reason') or env._("Undo is not available."))
        # The fingerprint covers what the preview SHOWED (current values, values to restore, conflicts), not which
        # fields were ticked: any change to the document since the preview refuses the Undo.
        if self._nc_fingerprint(log, analysis) != fingerprint:
            raise UserError(env._("The document changed since the preview. Review the Undo again."))
        mode = mode if mode in analysis['modes'] else analysis['modes'][0]
        vals = analysis.get('vals') or {}
        if log.nc_action == 'update' and not vals:
            raise UserError(env._("Choose at least one field to restore."))
        try:
            undo = self.sudo().create({
                'original_log_id': log.id, 'original_log_ref': log.id, 'kind': log.nc_action, 'user_id': env.uid,
                'model': log.model_model,
                'res_id': log.res_id, 'company_id': log.nc_company_id.id,
                'field_names': ','.join(sorted(vals)) if log.nc_action == 'update' else '',
            })
        except psycopg2.errors.UniqueViolation:
            raise UserError(env._("This event has already been undone.")) from None
        details = {}
        # the audit rows this operation writes belong to this Undo (server-side only, never a client context key)
        with undo_scope(undo.id):
            if log.nc_action == 'update':
                for name in vals:
                    details[name] = [V.display_current(env, record.sudo(), model._fields[name])[0],
                                     next(r['restore'] for r in analysis['rows'] if r['field'] == name)]
                record.write(_with_kept_values(record, vals))
                result_id = record.id
            elif mode == 'archive':
                record.write({'active': False})
                result_id = record.id
            elif log.nc_action == 'create':
                record.unlink()
                result_id = 0
            else:
                new = model.create(vals)
                result_id = new.id
        undo.sudo().write({'state': 'done', 'mode': mode, 'result_res_id': result_id,
                           'details': json.dumps(details, default=str)})
        # The operation's own audit rows exist when the document type is audited; when it is not (a model only an
        # OCA rule covered and that rule is off), this ledger row is the record of the Undo.
        _logger.info("Audit Undo: event %s (%s %s,%s) undone by uid %s, mode %s", log.id, log.nc_action,
                     log.model_model, log.res_id, env.uid, mode)
        return {'undo_id': undo.id, 'mode': mode, 'result_res_id': result_id,
                'log_ids': undo.result_log_ids.ids}
