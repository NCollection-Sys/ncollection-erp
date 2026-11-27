# -*- coding: utf-8 -*-
"""Audit Log — printable document versions (19.0.2.2.0).

A recorded version of a printable document is shown AS THE DOCUMENT, rendered by the document's own report (the same
QWeb template its Print button uses), not by a copy of it:

1. the recorded values of that version (header fields, invoice lines, for invoices the amounts of the Invoice Amount
   Audit) are laid over the stored document INSIDE A DATABASE SAVEPOINT, with plain SQL, so no business rule, sequence,
   mail, accounting check or audit capture runs;
2. the real report is rendered to HTML, as the reader (its normal access checks apply);
3. the savepoint is ROLLED BACK and the ORM cache cleared: nothing of the overlay can be stored, the row locks it took
   are released, and the next statement sees the document exactly as before.

Changed sections are found by comparing the two RENDERED documents (block by block, table rows by content) marked in
yellow - a real difference of what the document shows, never a guess from field names.

Honest limits, reported with each render (``limits``): a value nobody recorded is shown as it is today; an invoice line
deleted since cannot be laid back into the document (it is listed under it); a deleted document has nothing left to
render and is shown as a document card from its recorded values.
"""
import difflib
import logging
import re

import psycopg2
from markupsafe import escape
from lxml import etree, html as lxml_html

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.http import request
from odoo.tools import SQL
from odoo.tools.misc import file_open

from . import audit_values as V
from .audit_service import MAX_HISTORY

_logger = logging.getLogger(__name__)

# Reports bound to a model that are NOT the document itself (labels, barcodes, badges, previews, CVs, ...):
# such a model keeps the structured data view.
NOT_A_DOCUMENT = re.compile(r'(label|barcode|badge|preview|hash_integrity|_cv$|employee_cv|generated_by_odoo|_raw$|'
                            r'packages|pro_forma|original_vendor_bill)', re.I)
# The report a model's document is shown with when it has several (the plain document, not a variant).
PREFERRED = {
    'account.move': 'account.report_invoice',
    'account.payment': 'account.report_payment_receipt',
    'sale.order': 'sale.report_saleorder',
    'purchase.order': 'purchase.report_purchaseorder',
    'stock.picking': 'stock.report_deliveryslip',
}
INVOICE_TYPES = ('out_invoice', 'out_refund', 'in_invoice', 'in_refund', 'out_receipt', 'in_receipt')
with file_open('ncollection_audit/static/src/audit_center/document_frame.css') as _css:
    HIGHLIGHT_CSS = '<style id="nc-au-highlight">%s</style>' % _css.read()
LEAF_BLOCKERS = ('.//div | .//p | .//table | .//main | .//section | .//article | .//ul | .//ol | .//address'
                 ' | .//h1 | .//h2 | .//h3 | .//h4 | .//h5 | .//h6')
BLOCK_TAGS = {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'address', 'dt', 'dd'}


def _text(element):
    return ' '.join(''.join(element.itertext()).split())


class AuditDocument(models.AbstractModel):
    _name = 'ncollection.audit.document'
    _description = 'Audit Log printable document versions'

    # =====================================================================
    # which documents are printable
    # =====================================================================

    @api.model
    def _nc_document_report(self, model):
        """The report that prints ``model`` as a document (its Print menu entry), or None. Decided from the reports
        actually installed: a model with only label / barcode / badge reports is not a printable document."""
        if not model or model not in self.env:
            return None
        reports = self.env['ir.actions.report'].sudo().search(
            [('model', '=', model), ('report_type', 'in', ('qweb-pdf', 'qweb-html'))], order='id')
        reports = reports.filtered(lambda r: not NOT_A_DOCUMENT.search(r.report_name or ''))
        # a report restricted to groups is shown only to a reader in one of them, as its Print entry is
        user = self.env.user
        reports = reports.filtered(lambda r: not r.group_ids or any(
            user.has_group(xid) for xid in r.group_ids.sudo().get_external_id().values() if xid))
        if not reports:
            return None
        preferred = reports.filtered(lambda r: r.report_name == PREFERRED.get(model))
        bound = reports.filtered('binding_model_id')
        return (preferred or bound or reports)[:1]

    @api.model
    def _nc_printable(self, model, record):
        """(report or None, reason when not printable). A journal ENTRY is an account.move without a document form."""
        report = self._nc_document_report(model)
        if not report:
            return None, self.env._("This kind of record has no printed document: its changes are shown as data.")
        if model == 'account.move' and record is not None and record.sudo().move_type not in INVOICE_TYPES:
            return None, self.env._("A journal entry has no printed document: its changes are shown as data.")
        return report, ''

    # =====================================================================
    # the state of a version (from the recorded history only)
    # =====================================================================

    @api.model
    def _nc_versions(self, model, res_id):
        Service = self.env['ncollection.audit.service']
        logs = Service._nc_history_logs(model, res_id)
        events = [(log, {(log.model_model, log.res_id, line.field_name): line for line in log.line_ids})
                  for log in logs]
        first_action, first_index, keys = {}, {}, []
        for index, (log, lines) in enumerate(events):
            first_action.setdefault((log.model_model, log.res_id), log.nc_action)
            first_index.setdefault((log.model_model, log.res_id), index)
            for key in lines:
                if key not in keys:
                    keys.append(key)
        return logs, events, first_action, first_index, keys

    @api.model
    def _nc_operation_end(self, logs, index):
        while (index + 1 < len(logs) and logs[index + 1].nc_parent_model
               and logs[index + 1].create_date == logs[index].create_date):
            index += 1
        return index

    @api.model
    def _nc_state(self, model, res_id, logs, events, first_action, first_index, keys, index):
        """{key: (raw, raw_text, known)} after version ``index`` (-1: before the first recorded event), the records
        that existed then, and the invoice amounts (from the Invoice Amount Audit) - recorded facts only."""
        Service = self.env['ncollection.audit.service']
        state, alive, gone = {}, {}, {}
        for key in keys:
            if index < 0:
                raw, text, known = Service._nc_state_after(events, -1, key, first_action) if events else (
                    None, None, False)
            else:
                raw, text, known = Service._nc_state_after(events, index, key, first_action)
            state[key] = (raw, text, known)
        for (rec_model, rec_id), first in first_index.items():
            created_later = first_action[(rec_model, rec_id)] == 'create' and first > index
            deleted = any(log.nc_action == 'delete' and log.model_model == rec_model and log.res_id == rec_id
                          for log, _l in events[:index + 1])
            alive[(rec_model, rec_id)] = not created_later and not deleted
            if alive[(rec_model, rec_id)] and rec_model in self.env \
                    and not self.env[rec_model].sudo().browse(rec_id).exists():
                gone[(rec_model, rec_id)] = True      # existed at this version, deleted since
        amounts = {}
        rows = Service._nc_amount_rows(model, res_id)
        if rows and index >= 0:
            later = [log.create_date for log in logs[index + 1:] if log.create_date > logs[index].create_date]
            cutoff = later[0] if later else None
            for name in ('amount_untaxed', 'amount_tax', 'amount_total'):
                value, known = Service._nc_amount_at(rows, cutoff, name)
                if known:
                    amounts[name] = value
        return state, alive, gone, amounts

    # =====================================================================
    # rendering a version with the document's own report
    # =====================================================================

    @api.model
    def _nc_column_value(self, field, raw):
        value = V.parse(field, raw)
        if field.type == 'many2one':
            return value or None
        if field.type in ('date', 'datetime'):
            return value or None
        if field.type in ('char', 'text', 'html', 'selection'):
            return value or None
        return value

    @api.model
    def _nc_overlay(self, record, state, alive, amounts, limits):
        """Lay the recorded values of a version over ``record`` with SQL (caller holds a savepoint)."""
        cr = self.env.cr
        for (rec_model, rec_id, name), (raw, _text_value, known) in state.items():
            if not known or rec_model not in self.env:
                continue
            owner = self.env[rec_model]
            field = owner._fields.get(name)
            if field is None or not field.store or field.translate or field.type not in V.SUPPORTED:
                continue
            if not alive.get((rec_model, rec_id), True) or not owner.sudo().browse(rec_id).exists():
                continue
            try:
                value = self._nc_column_value(field, raw) if raw is not None else (
                    [] if field.type == 'many2many' else None)
            except (ValueError, SyntaxError):
                limits.append(self.env._("A recorded value of %(field)s could not be read back; it is shown as it "
                                         "is today.", field=field.string))
                continue
            if field.type in ('many2one', 'many2many') and value:
                targets = [value] if field.type == 'many2one' else list(value)
                if len(self.env[field.comodel_name].sudo().browse(targets).exists()) != len(set(targets)):
                    limits.append(self.env._("A record %(field)s pointed to no longer exists; the value is shown "
                                             "as it is today.", field=field.string))
                    continue
            try:
                # one value that the database refuses today (a constraint, a unique name) never hides the version
                with cr.savepoint(flush=False):
                    if field.type == 'many2many':
                        cr.execute(SQL("DELETE FROM %s WHERE %s = %s", SQL.identifier(field.relation),
                                       SQL.identifier(field.column1), rec_id))
                        for other in value or []:
                            cr.execute(SQL("INSERT INTO %s (%s, %s) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                                           SQL.identifier(field.relation), SQL.identifier(field.column1),
                                           SQL.identifier(field.column2), rec_id, other))
                    elif field.column_type:
                        cr.execute(SQL("UPDATE %s SET %s = %s WHERE id = %s", SQL.identifier(owner._table),
                                       SQL.identifier(name), value, rec_id))
            except (psycopg2.errors.LockNotAvailable, psycopg2.errors.DeadlockDetected):
                raise
            except psycopg2.Error:
                limits.append(self.env._("A recorded value of %(field)s could not be read back; it is shown as it "
                                         "is today.", field=field.string))
        # records that did not exist yet at this version (an invoice line added later) are taken out of the document
        for (rec_model, rec_id), is_alive in alive.items():
            if not is_alive and rec_model in self.env and (rec_model, rec_id) != (record._name, record.id):
                if self.env[rec_model].sudo().browse(rec_id).exists():
                    try:
                        with cr.savepoint(flush=False):
                            cr.execute(SQL("DELETE FROM %s WHERE id = %s",
                                           SQL.identifier(self.env[rec_model]._table), rec_id))
                    except (psycopg2.errors.LockNotAvailable, psycopg2.errors.DeadlockDetected):
                        raise
                    except psycopg2.Error:
                        limits.append(self.env._("A line added after this version could not be taken out of the "
                                                 "document; it is shown."))
        if amounts and record._name == 'account.move':
            move = record.sudo()
            sign = -1 if move.move_type in ('out_refund', 'in_invoice', 'in_receipt') else 1

            def signed(amount):
                # the *_signed amounts are in the COMPANY currency: converted at the document's own date
                if move.currency_id == move.company_id.currency_id:
                    return sign * amount
                return sign * move.currency_id._convert(amount, move.company_id.currency_id, move.company_id,
                                                        move.invoice_date or move.date or fields.Date.today())
            cr.execute(SQL(
                "UPDATE account_move SET amount_untaxed = %s, amount_tax = %s, amount_total = %s, "
                "amount_untaxed_signed = %s, amount_tax_signed = %s, amount_total_signed = %s WHERE id = %s",
                amounts.get('amount_untaxed', record.amount_untaxed), amounts.get('amount_tax', record.amount_tax),
                amounts.get('amount_total', record.amount_total),
                signed(amounts.get('amount_untaxed', record.amount_untaxed)),
                signed(amounts.get('amount_tax', record.amount_tax)),
                signed(amounts.get('amount_total', record.amount_total)), record.id))

    @api.model
    def _nc_render(self, report, record, overlay=None):
        """The document's own report as HTML. With ``overlay`` (state, alive, amounts, limits) the version's values
        are applied in a savepoint that is ALWAYS rolled back."""
        env = self.env
        env.flush_all()
        savepoint = env.cr.savepoint(flush=False)
        try:
            if overlay:
                # a reader never waits on (or blocks) someone editing the document: the overlay's row locks are
                # given up at once; SET LOCAL ends with the savepoint
                env.cr.execute("SET LOCAL lock_timeout = '1500ms'")
                self._nc_overlay(record, *overlay)
                env.invalidate_all(flush=False)
                if record._name == 'account.move':
                    # price subtotal / total of the lines follow the overlaid quantities, prices, discounts, taxes
                    lines = record.with_env(env).invoice_line_ids
                    lines._compute_totals()
            content, _kind = env['ir.actions.report']._render_qweb_html(report.report_name, [record.id])
        except (psycopg2.errors.LockNotAvailable, psycopg2.errors.DeadlockDetected):
            savepoint.close(rollback=True)
            env.clear()
            raise UserError(env._("This document is being changed by someone else right now. Try again in a "
                                  "moment.")) from None
        finally:
            savepoint.close(rollback=True)
            env.clear()            # nothing of the overlay survives in the ORM cache or the recompute queue
        return content.decode() if isinstance(content, bytes) else content

    # =====================================================================
    # highlighting what changed between two rendered versions
    # =====================================================================

    @api.model
    def _nc_mark(self, before_html, after_html):
        """(before, after, changed count) with the changed blocks of ``after`` marked yellow and the rows that
        disappeared marked in ``before``. Tables are compared row by row on content (an added line shifts nothing)."""
        before, after = lxml_html.fromstring(before_html), lxml_html.fromstring(after_html)
        changed = 0

        def tables(root):
            return [t for t in root.iter('table')]

        def rows(table):
            return [r for r in table.iter('tr') if r.getparent() is not None and (
                r.getparent() is table or r.getparent().getparent() is table)]

        marked_rows_b, marked_rows_a = set(), set()
        for ta, tb in zip(tables(before), tables(after)):
            ra, rb = rows(ta), rows(tb)
            matcher = difflib.SequenceMatcher(a=[_text(r) for r in ra], b=[_text(r) for r in rb], autojunk=False)
            for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                if tag == 'equal':
                    continue
                for row in rb[j1:j2]:
                    row.set('class', (row.get('class', '') + ' nc-au-chg').strip())
                    marked_rows_b.add(row)
                    changed += 1
                for row in ra[i1:i2]:
                    mark = ' nc-au-was' if tag == 'delete' else ' nc-au-chg'
                    row.set('class', (row.get('class', '') + mark).strip())
                    marked_rows_a.add(row)
        # every other block: compared at the same place in the document
        tree_b, tree_a = after.getroottree(), before.getroottree()
        for element in after.iter():
            if not isinstance(element.tag, str) or element.tag == 'table' or element.xpath('ancestor::table'):
                continue
            # only the innermost block is marked: never a wrapper holding other blocks (the page, a section)
            leaf_div = element.tag in ('div', 'span', 'strong') and not element.xpath(LEAF_BLOCKERS)
            if element.tag not in BLOCK_TAGS and not leaf_div:
                continue
            text_b = _text(element)
            if not text_b:
                continue
            match = tree_a.xpath(tree_b.getpath(element))
            text_a = _text(match[0]) if match else None
            if text_a != text_b and not element.xpath('ancestor::*[contains(@class, "nc-au-chg")]'):
                element.set('class', (element.get('class', '') + ' nc-au-chg').strip())
                changed += 1
                if match:
                    match[0].set('class', (match[0].get('class', '') + ' nc-au-chg').strip())

        def out(root):
            return etree.tostring(root, encoding='unicode', method='html')
        return out(before), out(after), changed

    @api.model
    def _nc_page(self, content):
        """A self-contained page for the screen's sandboxed frame: the report's own styles (absolute URLs), the
        highlight styles, never a script."""
        # the host the user is on (platform subdomain or verified custom domain), so the styles load from there
        base = request.httprequest.host_url if request else ''
        base = base or self.env['ir.config_parameter'].sudo().get_param('web.base.url') or ''
        content = re.sub(r'<script\b[^>]*>.*?</script>', '', content, flags=re.S | re.I)
        head = '<base href="%s/" target="_blank">%s' % (escape(base.rstrip('/')), HIGHLIGHT_CSS)
        if '<head>' in content:
            return content.replace('<head>', '<head>' + head, 1)
        return '<html><head>%s</head><body>%s</body></html>' % (head, content)

    # =====================================================================
    # public calls (as the reader; every access check of the service applies)
    # =====================================================================

    @api.model
    def _nc_document_for(self, model, res_id):
        """(record, report, reason): the existing document and its report, or None with the reason."""
        record = self.env[model].browse(res_id) if model in self.env else None
        if record is None or not record.sudo().exists():
            return None, None, self.env._("The document no longer exists: it is shown from its recorded values.")
        if not record.has_access('read'):
            raise AccessError(self.env._("You are not allowed to see this document's history."))
        report, reason = self._nc_printable(model, record)
        return (record, report, reason) if report else (None, None, reason)

    @api.model
    def _nc_index_of(self, logs, log_id):
        ids = logs.ids
        if log_id not in ids:
            raise AccessError(self.env._("These versions are not part of this document's visible history."))
        return ids.index(log_id)

    @api.model
    def _nc_lost_lines(self, alive, state):
        """Invoice lines that existed at a version but are deleted now: their recorded values, listed under the
        document (they cannot be laid back into it)."""
        lost = []
        for (rec_model, rec_id), is_alive in alive.items():
            if rec_model != 'account.move.line' or not is_alive:
                continue
            if self.env[rec_model].sudo().browse(rec_id).exists():
                continue
            values = {}
            for (m, i, name), (raw, text, known) in state.items():
                if (m, i) == (rec_model, rec_id) and known and raw is not None:
                    field = self.env[m]._fields.get(name)
                    if field is not None:
                        values[V.label(self.env, m, name, field.string)] = V.display(
                            self.env, m, field, raw, text)[0]
            lost.append(' · '.join('%s: %s' % (label, value) for label, value in values.items() if value)
                        or self.env._("Line #%s", rec_id))
        return lost

    @api.model
    def _nc_render_version(self, model, res_id, record, report, logs, history, index, limits):
        """(html, lost lines) of version ``index`` (-1 = before the first recorded event)."""
        logs_, events, first_action, first_index, keys = history
        if len(logs_) >= MAX_HISTORY:
            limits.append(self.env._("This document has more recorded events than are read at once: versions are "
                                     "rebuilt from the first %s events only.", MAX_HISTORY))
        state, alive, _gone, amounts = self._nc_state(
            model, res_id, logs_, events, first_action, first_index, keys, index)
        if any(not known for (m, i, _n), (_r, _t, known) in state.items() if (m, i) == (model, res_id)):
            limits.append(self.env._("Some values of this version were not recorded (history older than the Audit "
                                     "Log): they are shown as they are today."))
        if model == 'account.move' and not amounts and self.env['ncollection.audit.service']._nc_amount_rows(
                model, res_id):
            limits.append(self.env._("No amount change was recorded before this version: totals are recomputed "
                                     "from its lines."))
        html_out = self._nc_render(report, record, overlay=(state, alive, amounts, limits))
        return html_out, self._nc_lost_lines(alive, state)

    @api.model
    def nc_audit_document_event(self, log_id):
        """What the Changes tab shows for one event: the document as it was after the event (create, update) or before
        it (delete), highlighted against the version before it; or why it is shown as data."""
        Service = self.env['ncollection.audit.service']
        if isinstance(log_id, bool) or not isinstance(log_id, int):
            raise UserError(self.env._("Malformed request."))
        log = Service._nc_log(log_id)
        model = log.nc_parent_model or log.model_model
        res_id = log.nc_parent_res_id or log.res_id
        record, report, reason = self._nc_document_for(model, res_id)
        if record is None:
            return {'printable': False, 'reason': reason, 'deleted': bool(self._nc_document_report(model))}
        history = self._nc_versions(model, res_id)
        logs = history[0]
        index = self._nc_index_of(logs, log.id)
        limits = []
        end = self._nc_operation_end(logs, index)
        # the state BEFORE this operation: after the last earlier operation (an earlier transaction time)
        prior = [j for j in range(index) if logs[j].create_date < logs[index].create_date]
        previous = prior[-1] if prior else -1
        after, lost = self._nc_render_version(model, res_id, record, report, logs, history, end, limits)
        changed, before = 0, None
        if log.nc_action == 'update' or (log.nc_parent_model and log.nc_action in ('create', 'delete')):
            before_html, lost_before = self._nc_render_version(model, res_id, record, report, logs, history,
                                                               previous, limits)
            before, after, changed = self._nc_mark(before_html, after)
            # a line that existed before this event and is deleted now (by this event or later) cannot be laid back
            # into the document: it is listed with its recorded values
            lost += [line for line in lost_before if line not in lost]
        return {
            'printable': True, 'report': report.name, 'action': log.nc_action,
            'after': self._nc_page(after), 'before': self._nc_page(before) if before else '',
            'changed': changed, 'limits': sorted(set(limits)), 'lost_lines': lost,
            'version': index + 1,
        }

    @api.model
    def nc_audit_document_compare(self, model, res_id, log_a, log_b):
        """Two versions of a printable document, rendered by its own report, the differences marked in both."""
        if not isinstance(model, str) or not all(
                isinstance(x, int) and not isinstance(x, bool) for x in (res_id, log_a, log_b)):
            raise UserError(self.env._("Malformed request."))
        self.env['ncollection.audit.service']._nc_check_reader()     # before anything is said about the record
        record, report, reason = self._nc_document_for(model, res_id)
        if record is None:
            return {'printable': False, 'reason': reason}
        history = self._nc_versions(model, res_id)
        logs = history[0]
        ia, ib = sorted((self._nc_index_of(logs, log_a), self._nc_index_of(logs, log_b)))
        limits = []
        html_a, lost_a = self._nc_render_version(model, res_id, record, report, logs, history,
                                                 self._nc_operation_end(logs, ia), limits)
        html_b, lost_b = self._nc_render_version(model, res_id, record, report, logs, history,
                                                 self._nc_operation_end(logs, ib), limits)
        marked_a, marked_b, changed = self._nc_mark(html_a, html_b)
        return {
            'printable': True, 'report': report.name, 'a': self._nc_page(marked_a), 'b': self._nc_page(marked_b),
            'changed': changed, 'limits': sorted(set(limits)), 'lost_a': lost_a, 'lost_b': lost_b,
            'version_a': ia + 1, 'version_b': ib + 1,
            'date_a': fields.Datetime.to_string(logs[ia].create_date),
            'date_b': fields.Datetime.to_string(logs[ib].create_date),
        }

    @api.model
    def nc_audit_printable(self, model, res_id):
        """Whether this document's history is shown as documents (for the screen to choose its layout)."""
        self.env['ncollection.audit.service']._nc_check_reader()
        if not isinstance(model, str) or isinstance(res_id, bool) or not isinstance(res_id, int):
            raise UserError(self.env._("Malformed request."))
        record, report, reason = self._nc_document_for(model, res_id)
        return {'printable': bool(report), 'reason': reason, 'report': report.name if report else ''}
