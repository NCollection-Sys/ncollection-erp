# -*- coding: utf-8 -*-
"""Audit Log — restore a document to a chosen VERSION (19.0.2.2.0).

The target state is the recorded state of that version (``ncollection.audit.document._nc_state``: recorded facts only).
Restoring it is the same controlled operation as an Undo, done for every field and line that differs from today:

* run AS THE USER through the ORM (rights, record rules, constraints, company checks, settings delegation all apply);
* refused outright for protected document types, for a document no longer existing, and for any finalised or locked
  accounting document (invoices and payments: draft only, outside every lock date) - with the correct workflow named;
* per field: the same checks as Undo (restricted, computed, system-set, truncated values are never written; a linked
  record that is gone is not restored); restoring an invoice line's product keeps that line's price, taxes, account
  and analytic split unless those are restored too;
* invoice lines added after the version are removed; lines deleted since are NEVER re-added (listed as refused);
* amounts are never written: the document's own computation recomputes them from the restored lines;
* previewed first; applied only if the document still matches the preview (fingerprint) and its row can be locked at
  once (NOWAIT); recorded in the Undo ledger (kind "version") and, through the ORM, as ordinary audit events linked to
  that ledger row.
"""
import hashlib
import json
import logging

import psycopg2

from odoo import api, models
from odoo.exceptions import UserError
from odoo.tools import SQL, mute_logger

from . import audit_values as V
from .audit_flags import undo_scope
from .audit_undo import PROTECTED, PROTECTED_PREFIX, _hint, _with_kept_values

_logger = logging.getLogger(__name__)


class _Recorded:
    """The recorded value as the per-field checks expect it (they look at ``old_value`` for truncation)."""

    def __init__(self, raw):
        self.old_value = raw


class AuditRestore(models.Model):
    _inherit = 'ncollection.audit.undo'

    @api.model
    def _nc_restore_analyse(self, model, res_id, log_id):
        env = self.env
        if not env.user.has_group('ncollection_audit.group_audit_undo'):
            return self._nc_refuse(env._("You are not allowed to restore audited documents."))
        Doc = env['ncollection.audit.document']
        history = Doc._nc_versions(model, res_id)            # the reader's own access is checked here
        logs = history[0]
        index = Doc._nc_index_of(logs, log_id)
        end = Doc._nc_operation_end(logs, index)
        if model not in env or model in PROTECTED or model.startswith(PROTECTED_PREFIX):
            return self._nc_refuse(env._("Restoring a version is not available for this document type."),
                                   _hint(env, model))
        record = env[model].with_context(active_test=False).browse(res_id)
        if not record.sudo().exists():
            return self._nc_refuse(env._("The document no longer exists."))
        if not record.has_access('write'):
            return self._nc_refuse(env._("You are not allowed to change this document."))
        locked = self._nc_locked(record)
        if locked:
            return self._nc_refuse(locked, _hint(env, model))
        state, alive, _gone, _amounts = Doc._nc_state(model, res_id, *history, end)
        if model == 'account.move':
            # recorded line prices are amounts in the currency of that version: never written into another one
            raw_currency = state.get((model, res_id, 'currency_id'), (None, None, False))
            if raw_currency[2] and raw_currency[0] is not None:
                try:
                    then = V.parse(record._fields['currency_id'], raw_currency[0])
                except (ValueError, SyntaxError):
                    then = None
                if then and then != record.sudo().currency_id.id:
                    return self._nc_refuse(env._("The currency of this document changed since this version: its "
                                                 "recorded prices cannot be restored in another currency."))
        rows, writes, removals = [], {}, []
        for (key_model, key_id, name), (raw, text, known) in state.items():
            if not known or not alive.get((key_model, key_id), True) or key_model not in env:
                continue
            owner = env[key_model]
            target_record = owner.with_context(active_test=False).browse(key_id)
            if not target_record.sudo().exists():
                continue
            field = owner._fields.get(name)
            if field is None:
                continue
            label = V.label(env, key_model, name, field.string)
            if key_model != model:
                label = env._("Line %(line)s · %(field)s", line=self._nc_line_name(target_record), field=label)
            try:
                target = V.parse(field, raw) if raw is not None else ([] if field.type == 'many2many' else False)
            except (ValueError, SyntaxError):
                continue
            if V.same(field, V.current(target_record.sudo(), field), target):
                continue
            hidden = V.masked(env, key_model, field)
            row = {'field': '%s:%s:%s' % (key_model, key_id, name), 'label': label, 'restorable': False,
                   'conflict': False, 'reason': '',
                   # a restricted field's values never reach the screen, refused or not
                   'current': env._("Hidden") if hidden else V.display_current(env, target_record.sudo(), field)[0],
                   'restore': env._("Hidden") if hidden else (
                       V.display(env, key_model, field, raw, text)[0] if raw is not None else '')}
            problem = self._nc_field_problem(owner, field, _Recorded(raw))
            if not problem:
                ok, problem = self._nc_target_exists(field, target)
                problem = None if ok else problem
            if problem:
                row['reason'] = problem
            else:
                row['restorable'] = True
                writes.setdefault((key_model, key_id), {})[name] = (
                    [(6, 0, target)] if field.type == 'many2many' else target)
            rows.append(row)
        # invoice lines added after the version are removed; lines deleted since are never re-added
        for (key_model, key_id), is_alive in alive.items():
            if key_model == model or key_model not in env:
                continue
            line = env[key_model].browse(key_id)
            exists_now = bool(line.sudo().exists())
            if not is_alive and exists_now:
                removable = line.has_access('unlink')
                rows.append({'field': '%s:%s:remove' % (key_model, key_id), 'restorable': removable,
                             'label': env._("Line %(line)s", line=self._nc_line_name(line)), 'conflict': False,
                             'current': env._("Present"), 'restore': env._("Removed (added after this version)"),
                             'reason': '' if removable else env._("You are not allowed to remove this line.")})
                if removable:
                    removals.append((key_model, key_id))
            elif is_alive and not exists_now:
                rows.append({'field': '%s:%s:readd' % (key_model, key_id), 'restorable': False, 'conflict': False,
                             'label': env._("Line deleted since this version"), 'current': env._("Absent"),
                             'restore': '',
                             'reason': env._("A deleted line is never re-added from the trail: add it again on "
                                             "the draft document.")})
        if not any(r['restorable'] for r in rows):
            result = self._nc_refuse(env._("Nothing of this version can be restored: the document already matches "
                                           "it, or every difference is protected.") if rows else
                                     env._("The document already matches this version."))
            result['rows'] = rows
            return result
        related = []
        if model == 'account.move':
            related.append(env._("Amounts and taxes are recomputed by the document from the restored lines; they "
                                 "are never written from the trail."))
            if any(r['restorable'] and r['field'] == '%s:%s:partner_id' % (model, res_id) for r in rows):
                related.append(env._("Restoring the customer or supplier recomputes the fiscal position, and with "
                                     "it the taxes of the lines."))
        return {'available': True, 'kind': 'version', 'modes': ['version'], 'rows': rows, 'writes': writes,
                'removals': removals, 'reason': '', 'hint': '', 'related': related, 'version': index + 1,
                'effect': env._("The document returns to version %(version)s for every field and line listed as "
                                "restorable. Everything else stays as it is.", version=index + 1)}

    @api.model
    def _nc_line_name(self, line):
        sudo = line.sudo()
        return (sudo.name or (sudo.product_id.display_name if 'product_id' in sudo._fields else '') or
                '#%s' % line.id) if 'name' in sudo._fields else '#%s' % line.id

    @api.model
    def _nc_restore_fingerprint(self, log_id, analysis):
        payload = {'log': log_id, 'rows': [(r['field'], r.get('current'), r.get('restore'), r.get('restorable'))
                                           for r in analysis.get('rows', [])]}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()

    @api.model
    def nc_restore_preview(self, model, res_id, log_id):
        if not isinstance(model, str) or any(
                isinstance(x, bool) or not isinstance(x, int) for x in (res_id, log_id)):
            raise UserError(self.env._("Malformed request."))
        analysis = self._nc_restore_analyse(model, res_id, log_id)
        analysis.pop('writes', None)
        analysis.pop('removals', None)
        analysis['fingerprint'] = self._nc_restore_fingerprint(log_id, analysis) if analysis.get('available') else ''
        return analysis

    @api.model
    def nc_restore_apply(self, model, res_id, log_id, fingerprint):
        env = self.env
        if (not isinstance(model, str) or not isinstance(fingerprint, str)
                or any(isinstance(x, bool) or not isinstance(x, int) for x in (res_id, log_id))):
            raise UserError(env._("Malformed request."))
        # no row is locked for a caller that may not restore, nor on a model that is not a stored document
        if not env.user.has_group('ncollection_audit.group_audit_undo'):
            raise UserError(env._("You are not allowed to restore audited documents."))
        if (model not in env or model in PROTECTED or model.startswith(PROTECTED_PREFIX)
                or env[model]._abstract or not env[model]._auto):
            raise UserError(env._("Restoring a version is not available for this document type."))
        if model in env:
            try:
                with mute_logger('odoo.sql_db'):
                    env.cr.execute(SQL("SELECT id FROM %s WHERE id = %s FOR UPDATE NOWAIT",
                                       SQL.identifier(env[model]._table), res_id))
            except psycopg2.errors.LockNotAvailable:
                raise UserError(env._("This document is being changed by someone else right now. Try again.")) \
                    from None
        analysis = self._nc_restore_analyse(model, res_id, log_id)
        if not analysis.get('available'):
            raise UserError(analysis.get('reason') or env._("This version cannot be restored."))
        if self._nc_restore_fingerprint(log_id, {'rows': analysis['rows']}) != fingerprint:
            raise UserError(env._("The document changed since the preview. Review the restore again."))
        # the lines written or removed are locked too, at once, or nothing happens
        touched = {}
        for key_model, key_id in list(analysis['writes']) + list(analysis['removals']):
            if key_model != model:
                touched.setdefault(key_model, set()).add(key_id)
        try:
            with mute_logger('odoo.sql_db'):
                for key_model, ids in touched.items():
                    env.cr.execute(SQL("SELECT id FROM %s WHERE id IN %s FOR UPDATE NOWAIT",
                                       SQL.identifier(env[key_model]._table), tuple(ids)))
        except psycopg2.errors.LockNotAvailable:
            raise UserError(env._("This document is being changed by someone else right now. Try again.")) from None
        log = env['auditlog.log'].sudo().browse(log_id)
        undo = self.sudo().create({
            'original_log_id': log.id, 'original_log_ref': log.id, 'kind': 'version', 'user_id': env.uid,
            'model': model, 'res_id': res_id, 'company_id': log.nc_company_id.id,
            'field_names': ','.join(sorted(r['field'] for r in analysis['rows'] if r['restorable']))[:2000],
        })
        details = {r['field']: [r.get('current'), r.get('restore')] for r in analysis['rows'] if r['restorable']}
        with undo_scope(undo.id):
            # the document itself first, then its lines (the lines follow the restored header)
            ordered = sorted(analysis['writes'].items(), key=lambda item: item[0][0] != model)
            for (key_model, key_id), vals in ordered:
                target = env[key_model].browse(key_id)
                target.write(_with_kept_values(target, vals))
            for key_model, key_id in analysis['removals']:
                env[key_model].browse(key_id).unlink()
        undo.sudo().write({'state': 'done', 'mode': 'version', 'result_res_id': res_id,
                           'details': json.dumps(details, default=str)})
        _logger.info("Audit restore: %s,%s restored to version %s (event %s) by uid %s", model, res_id,
                     analysis.get('version'), log_id, env.uid)
        return {'undo_id': undo.id, 'version': analysis.get('version'), 'log_ids': undo.result_log_ids.ids}
