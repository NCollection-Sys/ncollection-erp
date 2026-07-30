# -*- coding: utf-8 -*-
"""F2-T01: the native financial report engine (framework).

``ncollection.account.report`` is the ``AbstractModel`` every report wizard
inherits. It owns the common FILTERS (FPA §10), the ``account.move.line`` domain
they build, and the shared render/export: on-screen native list, QWeb PDF, and
``xlsxwriter`` XLSX — plus per-line drill-down to the underlying journal items.

Concrete reports (F2-T02+: GL, Trial Balance, …) subclass this and implement
``_nc_columns`` + ``_nc_compute_lines``. Odoo owns accounting (FPA §4/§6): this
engine only READS ``account.move.line`` and renders — it never posts or computes
tax (enforced by the F1-T02 engine-boundary guard).
"""
import base64
import io
import logging

from odoo import fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    import xlsxwriter
except ImportError:  # pragma: no cover - xlsxwriter ships with Odoo 19
    xlsxwriter = None


class NcollectionAccountReport(models.AbstractModel):
    _name = 'ncollection.account.report'
    _description = 'NCollection Financial Report Engine'

    # ---- common filters (FPA §10) ---------------------------------------
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company)
    date_from = fields.Date(
        required=True, string='From',
        default=lambda self: fields.Date.context_today(self).replace(month=1, day=1))
    date_to = fields.Date(
        required=True, string='To', default=fields.Date.context_today)
    journal_ids = fields.Many2many('account.journal', string='Journals')
    account_ids = fields.Many2many('account.account', string='Accounts')
    partner_ids = fields.Many2many('res.partner', string='Partners')
    target_move = fields.Selection(
        [('posted', 'Posted Entries'), ('all', 'All Entries')],
        default='posted', required=True, string='Target Moves')

    # ---- filters -> account.move.line domain ----------------------------

    def _nc_move_line_domain(self, account=None, partner=None):
        """The base journal-item domain for the current filters. Pass ``account``
        and/or ``partner`` to scope it to one account/partner (drill-down)."""
        self.ensure_one()
        domain = [
            ('company_id', '=', self.company_id.id),
            ('date', '>=', self.date_from),
            ('date', '<=', self.date_to),
            ('display_type', 'not in', ('line_section', 'line_note')),
        ]
        domain.append(('parent_state', '=', 'posted') if self.target_move == 'posted'
                      else ('parent_state', 'in', ('posted', 'draft')))
        if self.journal_ids:
            domain.append(('journal_id', 'in', self.journal_ids.ids))
        accounts = account or self.account_ids
        if accounts:
            domain.append(('account_id', 'in', accounts.ids))
        partners = partner or self.partner_ids
        if partners:
            domain.append(('partner_id', 'in', partners.ids))
        return domain

    # ---- report contract (concrete reports override) --------------------

    def _nc_report_title(self):
        return self.env._("Financial Report")

    def _nc_columns(self):
        """Ordered column defs: [{'key', 'label', 'type': char|monetary}]."""
        raise NotImplementedError

    def _nc_compute_lines(self):
        """Ordered rows: [{'label', 'account_id'(int|False), 'debit', 'credit',
        'balance', 'level'(int)}]. The engine renders these everywhere."""
        raise NotImplementedError

    # ---- on-screen: generate line records, open the native list ---------

    def action_view(self):
        self.ensure_one()
        Line = self.env['ncollection.account.report.line']
        # Clear only THIS user's previous run (the ir.rule already scopes the
        # search to create_uid; the explicit filter is belt-and-suspenders).
        Line.search([('report_model', '=', self._name),
                     ('create_uid', '=', self.env.uid)]).unlink()
        currency_id = self.company_id.currency_id.id
        # One batched create — preserves compute order (the list _order='id'
        # renders it identically to PDF/XLSX) and stays a single insert.
        created = Line.create([{
            'report_model': self._name,
            'report_res_id': self.id,
            'account_id': row.get('account_id') or False,
            'partner_id': row.get('partner_id') or False,
            'label': row.get('label') or '',
            'debit': row.get('debit', 0.0),
            'credit': row.get('credit', 0.0),
            'balance': row.get('balance', 0.0),
            'level': row.get('level', 0),
            'currency_id': currency_id,
        } for row in self._nc_compute_lines()])
        return {
            'type': 'ir.actions.act_window',
            'name': self._nc_report_title(),
            'res_model': 'ncollection.account.report.line',
            'view_mode': 'list',
            'domain': [('id', 'in', created.ids)],
            'target': 'current',
        }

    # ---- PDF (native qweb-pdf) ------------------------------------------

    def action_export_pdf(self):
        self.ensure_one()
        return self.env.ref(
            'ncollection_account_reports.action_report_financial'
        ).report_action(self, config=False)

    # ---- XLSX (xlsxwriter — no OCA dep) ---------------------------------

    def action_export_xlsx(self):
        self.ensure_one()
        if xlsxwriter is None:
            raise UserError(self.env._(
                "XLSX export needs the 'xlsxwriter' Python library."))
        data = self._nc_build_xlsx()
        attachment = self.env['ir.attachment'].create({
            'name': '%s.xlsx' % self._nc_report_title(),
            'type': 'binary',
            'datas': data,
            'res_model': self._name,
            'res_id': self.id,
            'mimetype': ('application/vnd.openxmlformats-officedocument.'
                         'spreadsheetml.sheet'),
        })
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%s?download=true' % attachment.id,
            'target': 'self',
        }

    def _nc_sheet_name(self):
        """Excel forbids : \\ / ? * [ ] in sheet names and caps them at 31 chars.
        Every F2-T02+ report inherits this, so sanitise once here."""
        title = self._nc_report_title()
        for ch in ':\\/?*[]':
            title = title.replace(ch, ' ')
        return (title.strip() or 'Report')[:31]

    def _nc_build_xlsx(self):
        """Render the report to an XLSX workbook (base64 bytes)."""
        self.ensure_one()
        columns = self._nc_columns()
        lines = self._nc_compute_lines()
        stream = io.BytesIO()
        book = xlsxwriter.Workbook(stream, {'in_memory': True})
        sheet = book.add_worksheet(self._nc_sheet_name())
        f_title = book.add_format({'bold': True, 'font_size': 14})
        f_head = book.add_format({'bold': True, 'bottom': 1, 'bg_color': '#F2F2F2'})
        f_money = book.add_format({'num_format': '#,##0.00'})
        sheet.write(0, 0, self._nc_report_title(), f_title)
        sheet.write(1, 0, '%s → %s' % (self.date_from, self.date_to))
        header_row = 3
        for col, cdef in enumerate(columns):
            sheet.write(header_row, col, cdef['label'], f_head)
            sheet.set_column(col, col, 22 if col == 0 else 16)
        for i, row in enumerate(lines):
            r = header_row + 1 + i
            for col, cdef in enumerate(columns):
                val = row.get(cdef['key'], '')
                if cdef.get('type') == 'monetary':
                    sheet.write_number(r, col, val or 0.0, f_money)
                else:
                    sheet.write(r, col, val or '')
        book.close()
        return base64.b64encode(stream.getvalue())
