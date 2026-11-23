# -*- coding: utf-8 -*-
"""Audit Log 2.0: the read side (history list, event details, record timeline, version comparison).

Every method runs AS THE CALLING USER. What a user may see is decided in three layers, each enforced here or by the
ORM, never by the screen:

1. the ``auditlog`` ACL (``auditlog.group_auditlog_user``: Workspace Administrators and system administrators);
2. the COMPANY record rule on ``auditlog.log`` (security/audit_security.xml): an event of a company the user does not
   belong to is not returned, not counted, and not reachable by id;
3. the MODEL rule (``_nc_readable_model_ids``): an event about a document type the user cannot read in the ERP itself
   is not shown; and the FIELD rule (audit_values.masked): a restricted field's values are replaced by "Hidden".

Opening an event of a document that still exists also requires read access to THAT document (its own record rules).
"""
import logging
from datetime import datetime, time, timedelta

import pytz

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.translate import LazyTranslate

from . import audit_values as V

_logger = logging.getLogger(__name__)

_lt = LazyTranslate(__name__)

MAX_PAGE = 200
MAX_HISTORY = 1000
SORTS = {
    'date': 'create_date', 'user': 'user_id', 'document': 'name', 'type': 'model_model', 'action': 'nc_action',
    'company': 'nc_company_id',
}
ACTION_LABELS = {'create': _lt("Create"), 'update': _lt("Update"), 'delete': _lt("Delete"), 'other': _lt("Other")}


class AuditService(models.AbstractModel):
    _name = 'ncollection.audit.service'
    _description = 'Audit Log 2.0 service'

    # ---- access ----------------------------------------------------------

    @api.model
    def _nc_check_reader(self):
        if not self.env.user.has_group('auditlog.group_auditlog_user'):
            raise AccessError(self.env._("You are not allowed to read the Audit Log."))

    @api.model
    def _nc_readable_model_ids(self):
        """ir.model ids of the audited document types this user may read in the ERP itself."""
        self.env.cr.execute("SELECT DISTINCT model_id FROM auditlog_log WHERE model_id IS NOT NULL")
        ids = [row[0] for row in self.env.cr.fetchall()]
        result = []
        for model in self.env['ir.model'].sudo().browse(ids).exists():
            if model.model in self.env:
                if self.env[model.model].has_access('read'):
                    result.append(model.id)
            elif self.env.user.has_group('base.group_system'):
                result.append(model.id)          # a document type whose app was removed: administrators only
        return result

    @api.model
    def _nc_base_domain(self):
        return [('model_id', 'in', self._nc_readable_model_ids()), ('method', 'in', ('create', 'write', 'unlink'))]

    @api.model
    def _nc_log(self, log_id):
        """The event as this user may see it, or AccessError (never a hint that it exists)."""
        self._nc_check_reader()
        if not isinstance(log_id, int) or isinstance(log_id, bool):
            raise UserError(self.env._("Malformed request."))
        log = self.env['auditlog.log'].search(self._nc_base_domain() + [('id', '=', log_id)], limit=1)
        if not log:
            raise AccessError(self.env._("This audit event does not exist or you are not allowed to see it."))
        return log

    @api.model
    def _nc_document(self, log):
        """(record or None, readable). None when the document no longer exists (or its app was removed)."""
        if not log.model_model or log.model_model not in self.env or not log.res_id:
            return None, False
        record = self.env[log.model_model].with_context(active_test=False).browse(log.res_id)
        if not record.sudo().exists():
            return None, False
        return record, record.has_access('read')

    # ---- filters ---------------------------------------------------------

    @api.model
    def _nc_utc(self, value, end=False):
        if not value:
            return None
        try:
            day = fields.Date.to_date(value)
        except (ValueError, TypeError):
            raise UserError(self.env._("Malformed request.")) from None
        tz = pytz.timezone(self.env.user.tz or 'UTC')
        moment = datetime.combine(day + timedelta(days=1) if end else day, time.min)
        return tz.localize(moment).astimezone(pytz.utc).replace(tzinfo=None)

    @api.model
    def _nc_ids(self, values):
        if not values:
            return []
        if not isinstance(values, list) or len(values) > 500 or any(
                isinstance(v, bool) or not isinstance(v, int) for v in values):
            raise UserError(self.env._("Malformed request."))
        return values

    @api.model
    def _nc_domain(self, filters):
        filters = filters or {}
        if not isinstance(filters, dict):
            raise UserError(self.env._("Malformed request."))
        domain = self._nc_base_domain()
        start, stop = self._nc_utc(filters.get('date_from')), self._nc_utc(filters.get('date_to'), end=True)
        if start:
            domain.append(('create_date', '>=', start))
        if stop:
            domain.append(('create_date', '<', stop))
        if filters.get('user_ids'):
            domain.append(('user_id', 'in', self._nc_ids(filters['user_ids'])))
        actions = [a for a in (filters.get('actions') or []) if a in ACTION_LABELS]
        if actions:
            domain.append(('nc_action', 'in', actions))
        types = [t for t in (filters.get('doc_types') or []) if isinstance(t, str) and len(t) <= 128]
        if types:
            exact = [t for t in types if ':' in t]
            models_ = [t for t in types if ':' not in t]
            if exact and models_:
                domain += ['|', ('nc_doc_type', 'in', exact), ('model_model', 'in', models_)]
            elif exact:
                domain.append(('nc_doc_type', 'in', exact))
            else:
                domain.append(('model_model', 'in', models_))
        if filters.get('company_ids'):
            domain.append(('nc_company_id', 'in', self._nc_ids(filters['company_ids'])))
        reference = filters.get('reference')
        if isinstance(reference, str) and reference.strip():
            domain.append(('name', 'ilike', reference.strip()[:128]))
        record = filters.get('record')
        if isinstance(record, dict) and record.get('model') and record.get('res_id'):
            if not isinstance(record['res_id'], int) or isinstance(record['res_id'], bool):
                raise UserError(self.env._("Malformed request."))
            domain += [('model_model', '=', str(record['model'])[:128]), ('res_id', '=', record['res_id'])]
        elif isinstance(filters.get('res_id'), int) and not isinstance(filters.get('res_id'), bool):
            domain.append(('res_id', '=', filters['res_id']))
        text = filters.get('search')
        if isinstance(text, str) and text.strip():
            text = text.strip()[:128]
            domain += ['|', '|', ('name', 'ilike', text), ('model_name', 'ilike', text),
                       ('line_ids.field_description', 'ilike', text)]
        reversed_ = filters.get('reversed')
        undone = self.env['ncollection.audit.undo'].sudo().search(
            [('original_log_ref', '!=', 0)]).mapped('original_log_ref')
        if reversed_ == 'reversed':
            domain.append(('id', 'in', undone))
        elif reversed_ == 'not_reversed':
            domain.append(('id', 'not in', undone))
        elif reversed_ == 'undo_events':
            domain.append(('nc_undo_id', '!=', False))
        return domain

    # ---- rows ------------------------------------------------------------

    @api.model
    def _nc_description(self, log, label):
        name = log.name or ('#%s' % log.res_id)
        if log.nc_action == 'create':
            text = self.env._("Created %(type)s %(name)s", type=label, name=name)
        elif log.nc_action == 'delete':
            text = self.env._("Deleted %(type)s %(name)s", type=label, name=name)
        else:
            labels = [V.label(self.env, log.model_model, line.field_name, line.field_description)
                      for line in log.line_ids[:3]]
            more = len(log.line_ids) - len(labels)
            text = self.env._("Changed %(fields)s", fields=', '.join(labels)) if labels else self.env._("Updated")
            if more > 0:
                text += ' ' + self.env._("(+%(count)s more)", count=more)
        if log.nc_undo_id:
            text = self.env._("Undo of event #%(id)s: %(text)s", id=log.nc_undo_id.original_log_ref, text=text)
        return text

    @api.model
    def _nc_row(self, log, undone):
        label = V.doc_label(self.env, log.nc_doc_type or log.model_model, log.model_name)
        return {
            'id': log.id,
            'date': fields.Datetime.to_string(log.create_date),
            'user': log.user_id.sudo().name or '',
            'user_id': log.user_id.id,
            'document': log.name or ('#%s' % log.res_id),
            'model': log.model_model or '',
            'res_id': log.res_id,
            'type': label,
            'description': self._nc_description(log, label),
            'action': log.nc_action or 'other',
            'company': log.nc_company_id.sudo().name or '',
            'changes': len(log.line_ids),
            'reversed': log.id in undone,
            'undo_event': bool(log.nc_undo_id),
        }

    @api.model
    def nc_audit_search(self, filters=None, offset=0, limit=50, sort='date', direction='desc'):
        """One page of the history. Newest first unless the caller asks otherwise; the id is always the tie-breaker,
        so two events in the same second keep a stable order across pages."""
        self._nc_check_reader()
        if (isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or isinstance(limit, bool)
                or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE):
            raise UserError(self.env._("Malformed request."))
        column = SORTS.get(sort, 'create_date')
        way = 'asc' if direction == 'asc' else 'desc'
        domain = self._nc_domain(filters)
        Log = self.env['auditlog.log']
        total = Log.search_count(domain)
        logs = Log.search(domain, offset=offset, limit=limit, order='%s %s, id %s' % (column, way, way))
        undone = set(self.env['ncollection.audit.undo'].sudo().search(
            [('original_log_ref', 'in', logs.ids)]).mapped('original_log_ref'))
        return {'total': total, 'rows': [self._nc_row(log, undone) for log in logs]}

    @api.model
    def nc_audit_options(self):
        """What the filters can offer: only users, document types and companies that occur in events THIS user may
        see (deactivated users included - they still did what they did)."""
        self._nc_check_reader()
        domain = self._nc_base_domain()
        Log = self.env['auditlog.log']
        users = Log._read_group(domain, ['user_id'], ['__count'])
        types = Log._read_group(domain, ['nc_doc_type', 'model_model', 'model_name'], ['__count'])
        companies = Log._read_group(domain + [('nc_company_id', '!=', False)], ['nc_company_id'], ['__count'])
        seen, doc_types = set(), []
        for key, model, model_name, _count in types:
            value = key or model
            if not value or value in seen:
                continue
            seen.add(value)
            doc_types.append({'key': value, 'label': V.doc_label(self.env, value, model_name)})
        doc_types.sort(key=lambda item: item['label'].lower())
        return {
            'users': sorted(({'id': u.id, 'name': u.sudo().name, 'active': u.sudo().active}
                             for u, _c in users if u), key=lambda item: (item['name'] or '').lower()),
            'doc_types': doc_types,
            'companies': [{'id': c.id, 'name': c.sudo().name} for c, _c in companies if c],
            'actions': [{'key': k, 'label': self.env._(v)} for k, v in ACTION_LABELS.items() if k != 'other'],
            'can_undo': self.env.user.has_group('ncollection_audit.group_audit_undo'),
            'multi_company': len(self.env.user.company_ids) > 1,
        }

    # ---- one event -------------------------------------------------------

    @api.model
    def _nc_lines(self, log, record):
        model = self.env[log.model_model] if log.model_model in self.env else None
        currency = V.currency_of(record.sudo()) if record is not None else (
            log.nc_company_id.sudo().currency_id or self.env.company.currency_id)
        rows = []
        for line in log.line_ids.sorted(lambda ln: (ln.field_description or ln.field_name or '').lower()):
            field = model._fields.get(line.field_name) if model is not None else None
            label = V.label(self.env, log.model_model, line.field_name, line.field_description)
            if field is None:
                # a field that no longer exists: its restriction cannot be checked, so only administrators see it,
                # and a credential by name never
                hidden = (not self.env.user.has_group('base.group_system')
                          or V.SECRET_NAME.search(line.field_name or ''))
                text = self.env._("Hidden (restricted field)")
                rows.append({'field': line.field_name, 'label': label,
                             'before': text if hidden else (line.old_value_text or ''),
                             'after': text if hidden else (line.new_value_text or ''),
                             'before_kind': 'masked' if hidden else 'value',
                             'after_kind': 'masked' if hidden else 'value', 'type': 'unknown'})
                continue
            before, before_kind = V.display(self.env, log.model_model, field, line.old_value, line.old_value_text,
                                            currency)
            after, after_kind = V.display(self.env, log.model_model, field, line.new_value, line.new_value_text,
                                          currency)
            rows.append({'field': field.name, 'label': label, 'before': before, 'after': after,
                         'before_kind': before_kind, 'after_kind': after_kind, 'type': field.type})
        return rows

    @api.model
    def nc_audit_event(self, log_id):
        log = self._nc_log(log_id)
        record, readable = self._nc_document(log)
        if record is not None and not readable:
            raise AccessError(self.env._("You are not allowed to see this document's history."))
        undo = self.env['ncollection.audit.undo'].sudo().search([('original_log_ref', '=', log.id)], limit=1)
        label = V.doc_label(self.env, log.nc_doc_type or log.model_model, log.model_name)
        technical = self.env.user.has_group('base.group_system')
        return {
            **self._nc_row(log, {log.id} if undo else set()),
            'lines': self._nc_lines(log, record),
            'exists': record is not None,
            'type': label,
            'request': (log.http_request_id.sudo().name or '') if technical else '',
            'session': (log.http_session_id.sudo().display_name or '') if technical else '',
            'ip': (log.nc_remote_addr or '') if technical else '',
            'technical_model': log.model_model if technical else '',
            'source': dict(log._fields['nc_source']._description_selection(self.env)).get(log.nc_source, ''),
            'undone_by': {'id': undo.id, 'user': undo.user_id.name, 'date': fields.Datetime.to_string(
                undo.create_date), 'log_ids': undo.result_log_ids.ids} if undo else False,
            'undo_of': {'id': log.nc_undo_id.original_log_ref} if log.nc_undo_id else False,
            'can_undo': self.env.user.has_group('ncollection_audit.group_audit_undo'),
        }

    # ---- record history & versions ---------------------------------------

    @api.model
    def _nc_history_logs(self, model, res_id):
        self._nc_check_reader()
        if (not isinstance(model, str) or len(model) > 128 or isinstance(res_id, bool)
                or not isinstance(res_id, int)):
            raise UserError(self.env._("Malformed request."))
        if model in self.env:
            record = self.env[model].with_context(active_test=False).browse(res_id)
            if record.sudo().exists() and not record.has_access('read'):
                raise AccessError(self.env._("You are not allowed to see this document's history."))
        return self.env['auditlog.log'].search(
            self._nc_base_domain() + [('model_model', '=', model), ('res_id', '=', res_id)],
            order='create_date asc, id asc', limit=MAX_HISTORY)

    @api.model
    def nc_audit_history(self, model, res_id):
        logs = self._nc_history_logs(model, res_id)
        undone = set(self.env['ncollection.audit.undo'].sudo().search(
            [('original_log_ref', 'in', logs.ids)]).mapped('original_log_ref'))
        first = logs[:1]
        exists = model in self.env and bool(self.env[model].sudo().browse(res_id).exists())
        versions = []
        for number, log in enumerate(logs, start=1):
            row = self._nc_row(log, undone)
            row['version'] = number
            row['fields'] = [V.label(self.env, log.model_model, line.field_name, line.field_description)
                             for line in log.line_ids]
            versions.append(row)
        return {
            'model': model, 'res_id': res_id, 'exists': exists,
            'document': (logs[-1:].name or first.name or '#%s' % res_id) if logs else '#%s' % res_id,
            'type': V.doc_label(self.env, (logs[-1:].nc_doc_type or model) if logs else model),
            'complete': bool(first) and first.nc_action == 'create',
            'truncated': len(logs) >= MAX_HISTORY,
            'versions': versions,
        }

    @api.model
    def _nc_state_after(self, events, index, name):
        """The value of field ``name`` right after event ``index`` (0-based), as (raw, raw_text, known).

        Exact from what was recorded: the last event at or before ``index`` that recorded the field gives its NEW
        value; failing that, the first LATER event that recorded it gives its OLD value (the value it had all along).
        A field recorded by neither is unknown - unless the history starts with a full creation record, which states
        every non-empty value, so a field it does not list was empty."""
        for log, lines in reversed(events[:index + 1]):
            line = lines.get(name)
            if line is not None:
                if log.nc_action == 'delete':
                    return line.old_value, line.old_value_text, True
                return line.new_value, line.new_value_text, True
        for log, lines in events[index + 1:]:
            line = lines.get(name)
            if line is not None and log.nc_action != 'create':
                return line.old_value, line.old_value_text, True
        if events and events[0][0].nc_action == 'create':
            return None, None, True
        return None, None, False

    @api.model
    def nc_audit_compare(self, model, res_id, log_a, log_b):
        """Version A vs version B of one document, from recorded history only (never guessed)."""
        logs = self._nc_history_logs(model, res_id)
        ids = logs.ids
        if log_a not in ids or log_b not in ids:
            raise AccessError(self.env._("These versions are not part of this document's visible history."))
        ia, ib = sorted((ids.index(log_a), ids.index(log_b)))
        events = [(log, {line.field_name: line for line in log.line_ids}) for log in logs]
        names = []
        for _log, lines in events:
            for name in lines:
                if name not in names:
                    names.append(name)
        Model = self.env[model] if model in self.env else None
        record = Model.with_context(active_test=False).browse(res_id).sudo().exists() if Model is not None else None
        currency = V.currency_of(record) if record else self.env.company.currency_id
        gaps, rows = [], []
        for name in names:
            field = Model._fields.get(name) if Model is not None else None
            if field is None:
                continue
            ra, ta, ka = self._nc_state_after(events, ia, name)
            rb, tb, kb = self._nc_state_after(events, ib, name)
            da, kind_a = V.display(self.env, model, field, ra, ta, currency) if ka else (
                self.env._("Not recorded"), 'unknown')
            db, kind_b = V.display(self.env, model, field, rb, tb, currency) if kb else (
                self.env._("Not recorded"), 'unknown')
            if not ka or not kb:
                status = 'unknown'
            elif kind_a == 'empty' and kind_b != 'empty':
                status = 'added'
            elif kind_a != 'empty' and kind_b == 'empty':
                status = 'removed'
            else:
                try:
                    status = 'unchanged' if V.same(field, V.parse(field, ra), V.parse(field, rb)) else 'changed'
                except (ValueError, SyntaxError):
                    status = 'unchanged' if (ra or '') == (rb or '') else 'changed'
            rows.append({'field': name, 'label': V.label(self.env, model, name, field.string), 'a': da, 'b': db,
                         'status': status,
                         'a_kind': kind_a, 'b_kind': kind_b})
        # GAP DETECTION: an event whose recorded OLD value is not the previous event's NEW value means something
        # changed the field without being recorded (auditing switched off, a raw SQL write, a legacy period).
        last = {}
        for log, lines in events:
            for name, line in lines.items():
                field = Model._fields.get(name) if Model is not None else None
                if field is None:
                    continue
                if name in last and log.nc_action == 'update':
                    try:
                        if not V.same(field, V.parse(field, last[name]), V.parse(field, line.old_value)):
                            gaps.append({'field': V.label(self.env, model, name, field.string), 'event': log.id})
                    except (ValueError, SyntaxError):
                        # a value that cannot be read back is not evidence of a gap either way
                        _logger.debug("audit compare: unreadable value of %s on event %s", name, log.id)
                last[name] = line.new_value if log.nc_action != 'delete' else line.old_value
        rows.sort(key=lambda r: ({'changed': 0, 'added': 1, 'removed': 2, 'unknown': 3, 'unchanged': 4}[r['status']],
                                 (r['label'] or '').lower()))
        return {
            'a': {'id': logs[ia].id, 'version': ia + 1, 'date': fields.Datetime.to_string(logs[ia].create_date),
                  'action': logs[ia].nc_action, 'user': logs[ia].user_id.sudo().name or ''},
            'b': {'id': logs[ib].id, 'version': ib + 1, 'date': fields.Datetime.to_string(logs[ib].create_date),
                  'action': logs[ib].nc_action, 'user': logs[ib].user_id.sudo().name or ''},
            'rows': rows,
            'summary': {status: sum(1 for r in rows if r['status'] == status)
                        for status in ('changed', 'added', 'removed', 'unchanged', 'unknown')},
            'complete': events[0][0].nc_action == 'create',
            'gaps': gaps,
        }

    # ---- navigation ------------------------------------------------------

    @api.model
    def nc_audit_open_document(self, log_id):
        """An action opening the document of an event, only when it exists and the user may read it."""
        log = self._nc_log(log_id)
        record, readable = self._nc_document(log)
        if record is None or not readable:
            raise UserError(self.env._("This document no longer exists or you cannot open it."))
        return {'type': 'ir.actions.act_window', 'res_model': record._name, 'res_id': record.id,
                'views': [(False, 'form')], 'target': 'current'}

    @api.model
    def action_open_history(self, model, res_ids):
        """The contextual "Audit History" action (bound to the audited document types)."""
        self._nc_check_reader()
        res_id = res_ids[0] if res_ids else 0
        return {
            'type': 'ir.actions.client', 'tag': 'ncollection_audit.audit_center',
            'name': self.env._("Audit History"),
            'context': {'nc_audit_record': {'model': model, 'res_id': res_id}},
        }
