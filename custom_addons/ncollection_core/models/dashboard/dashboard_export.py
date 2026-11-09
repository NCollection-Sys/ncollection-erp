# -*- coding: utf-8 -*-
"""DASH-02: Export the Summary dashboard as PDF or Excel.

NO figure is computed here. The export renders the payload ``ncollection.dashboard.data.get_dashboard_payload`` returns
for the SAME user, company and period, so the file can only ever say what the screen says: the role gating, the
licence gating, the access rules and every validated calculation are the dashboard's own. This module only lays that
payload out.

Excel: every number is a number cell (formatted with the company currency), the period are date cells, and every text
is written as a STRING cell -- a value that begins with ``= + - @`` (or a tab / line break) is additionally prefixed
with an apostrophe, so a customer or product name can never be evaluated as a formula by a spreadsheet application.
PDF: one HTML document through Odoo's wkhtmltopdf, right-to-left when the user's language is.
"""
import base64
import io
import re
from datetime import datetime
from html import escape

from odoo import api, fields, models
from odoo.tools.misc import formatLang

try:
    import xlsxwriter
except ImportError:  # pragma: no cover - xlsxwriter ships with Odoo 19
    xlsxwriter = None

_FORMULA_STARTS = ('=', '+', '-', '@', '\t', '\r', '\n')
_SHEET_FORBIDDEN = re.compile(r'[\[\]:*?/\\]')
_FILE_UNSAFE = re.compile(r'[^A-Za-z0-9_-]+')


def safe_text(value):
    """Text for a spreadsheet cell: never something a spreadsheet application would evaluate as a formula."""
    text = '' if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_STARTS) else text


class DashboardExport(models.AbstractModel):
    _name = 'ncollection.dashboard.export'
    _description = 'NCollection Dashboard Export'

    # ------------------------------------------------------------------ the payload (the dashboard's own)
    @api.model
    def _export_payload(self, date_from=None, date_to=None):
        return self.env['ncollection.dashboard.data'].get_dashboard_payload(date_from, date_to)

    @api.model
    def _is_rtl(self):
        lang = self.env['res.lang']._lang_get(self.env.lang or self.env.user.lang)
        return bool(lang and lang.direction == 'rtl')

    @api.model
    def export_filename(self, payload, extension):
        meta = payload['meta']
        stem = 'NCollection-Summary-%s-%s_%s' % (
            _FILE_UNSAFE.sub('-', meta.get('company_name') or 'company').strip('-') or 'company',
            meta['period']['date_from'], meta['period']['date_to'])
        return '%s.%s' % (stem[:120], extension)

    # ------------------------------------------------------------------ Excel
    @api.model
    def build_xlsx(self, payload):
        if xlsxwriter is None:  # pragma: no cover
            raise ImportError("xlsxwriter is required")
        meta = payload['meta']
        currency = self.env.company.currency_id
        symbol = (currency.symbol or currency.name or '').replace('"', '')
        money = '"%s" #,##0.00;[Red]-"%s" #,##0.00' % (symbol, symbol)
        stream = io.BytesIO()
        book = xlsxwriter.Workbook(stream, {'in_memory': True, 'strings_to_formulas': False,
                                            'strings_to_urls': False, 'strings_to_numbers': False})
        fmt = {
            'title': book.add_format({'bold': True, 'font_size': 14}),
            'head': book.add_format({'bold': True, 'bg_color': '#EEF2F7', 'border': 1}),
            'text': book.add_format({'text_wrap': False}),
            'money': book.add_format({'num_format': money}),
            'int': book.add_format({'num_format': '#,##0'}),
            'pct': book.add_format({'num_format': '0.0"%"'}),
            'date': book.add_format({'num_format': 'yyyy-mm-dd', 'align': 'left'}),
            'bold_money': book.add_format({'num_format': money, 'bold': True}),
        }
        rtl = self._is_rtl()
        names = set()

        def sheet(title):
            clean = _SHEET_FORBIDDEN.sub(' ', safe_text(title)).strip("' ")[:28] or 'Sheet'
            name, n = clean, 1
            while name.lower() in names:
                n += 1
                name = '%s %s' % (clean[:25], n)
            names.add(name.lower())
            ws = book.add_worksheet(name)
            if rtl:
                ws.right_to_left()
            return ws

        def amount_format(kind):
            return fmt['money'] if kind == 'currency' else fmt['int']

        # --- context
        ws = sheet(self.env._('Summary'))
        ws.set_column(0, 0, 28)
        ws.set_column(1, 1, 46)
        ws.write_string(0, 0, safe_text(self.env._('Summary dashboard')), fmt['title'])
        row = 2
        for label, value in (
                (self.env._('Company'), meta.get('company_name')),
                (self.env._('Currency'), currency.name),
                (self.env._('Exported by'), meta.get('user_name')),
                (self.env._('Exported at'), fields.Datetime.to_string(fields.Datetime.now()) + ' UTC')):
            ws.write_string(row, 0, safe_text(label), fmt['head'])
            ws.write_string(row, 1, safe_text(value), fmt['text'])
            row += 1
        for label, key in ((self.env._('From'), 'date_from'), (self.env._('To'), 'date_to')):
            ws.write_string(row, 0, safe_text(label), fmt['head'])
            ws.write_datetime(row, 1, datetime.strptime(meta['period'][key], '%Y-%m-%d'), fmt['date'])
            row += 1

        widgets = payload['widgets']
        # --- KPIs
        kpis = [w for w in widgets if w.get('type') == 'kpi']
        ws = sheet(self.env._('Key figures'))
        ws.set_column(0, 0, 32)
        ws.set_column(1, 1, 18)
        ws.set_column(2, 3, 30)
        for col, head in enumerate((self.env._('Metric'), self.env._('Value'), self.env._('Basis'),
                                    self.env._('Change vs last month (%)'))):
            ws.write_string(0, col, safe_text(head), fmt['head'])
        for i, w in enumerate(kpis, start=1):
            ws.write_string(i, 0, safe_text(w.get('label')), fmt['text'])
            ws.write_number(i, 1, float(w.get('value') or 0.0), amount_format(w.get('format')))
            ws.write_string(i, 2, safe_text(w.get('sub')), fmt['text'])
            if w.get('trend') is not None:
                ws.write_number(i, 3, float(w['trend']), fmt['pct'])
        ws.autofilter(0, 0, max(len(kpis), 1), 3)
        ws.freeze_panes(1, 0)

        # --- charts
        charts = [w for w in widgets if w.get('type') == 'chart']
        if charts:
            ws = sheet(self.env._('Charts'))
            ws.set_column(0, 1, 32)
            ws.set_column(2, 2, 18)
            for col, head in enumerate((self.env._('Chart'), self.env._('Label'), self.env._('Value'))):
                ws.write_string(0, col, safe_text(head), fmt['head'])
            row = 1
            for w in charts:
                for label, value in zip(w.get('labels') or (), w.get('data') or ()):
                    ws.write_string(row, 0, safe_text(w.get('label')), fmt['text'])
                    ws.write_string(row, 1, safe_text(label), fmt['text'])
                    ws.write_number(row, 2, float(value or 0.0), amount_format(w.get('format')))
                    row += 1
            ws.autofilter(0, 0, max(row - 1, 1), 2)
            ws.freeze_panes(1, 0)

        # --- statements
        for w in (w for w in widgets if w.get('type') == 'statement'):
            ws = sheet(w.get('label'))
            ws.set_column(0, 0, 46)
            ws.set_column(1, 1, 20)
            ws.write_string(0, 0, safe_text(w.get('label')), fmt['title'])
            ws.write_string(1, 0, safe_text(w.get('period')), fmt['text'])
            for col, head in enumerate((self.env._('Line'), self.env._('Amount'))):
                ws.write_string(3, col, safe_text(head), fmt['head'])
            for i, line in enumerate(w.get('rows') or (), start=4):
                indent = book.add_format({'indent': min(int(line.get('level') or 0), 6),
                                          'bold': not line.get('level')})
                ws.write_string(i, 0, safe_text(line.get('label')), indent)
                ws.write_number(i, 1, float(line.get('value') or 0.0),
                                fmt['bold_money'] if not line.get('level') else fmt['money'])
            ws.freeze_panes(4, 0)

        book.close()
        return stream.getvalue()

    # ------------------------------------------------------------------ PDF
    @api.model
    def _format_value(self, widget_format, value):
        if widget_format == 'currency':
            return formatLang(self.env, value or 0.0, currency_obj=self.env.company.currency_id)
        return formatLang(self.env, value or 0.0, digits=0)

    @api.model
    def build_html(self, payload):
        meta = payload['meta']
        E = escape
        env_ = self.env._
        widgets = payload['widgets']
        parts = []
        kpis = [w for w in widgets if w.get('type') == 'kpi']
        if kpis:
            heads = (E(env_('Key figures')), E(env_('Metric')), E(env_('Value')), E(env_('Basis')))
            parts.append('<h2>%s</h2><table class="nc-kpi"><thead><tr><th>%s</th><th class="n">%s</th>'
                         '<th>%s</th></tr></thead><tbody>' % heads)
            for w in kpis:
                negative = (w.get('value') or 0) < 0
                parts.append('<tr><td>%s</td><td class="n%s">%s</td><td class="m">%s</td></tr>' % (
                    E(str(w.get('label') or '')), ' neg' if negative else '',
                    E(self._format_value(w.get('format'), w.get('value'))), E(str(w.get('sub') or ''))))
            parts.append('</tbody></table>')
        for w in (w for w in widgets if w.get('type') == 'chart'):
            data = list(zip(w.get('labels') or (), w.get('data') or ()))
            peak = max([abs(v or 0) for _l, v in data] or [0]) or 1
            heads = (E(str(w.get('label') or '')), E(env_('Label')), E(env_('Value')))
            parts.append('<h2>%s</h2><table><thead><tr><th>%s</th><th class="n">%s</th><th></th></tr>'
                         '</thead><tbody>' % heads)
            for label, value in data:
                parts.append('<tr><td dir="auto">%s</td><td class="n">%s</td><td style="width:35%%"><span class="bar" '
                             'style="width:%d%%"></span></td></tr>' % (
                                 E(str(label)), E(self._format_value(w.get('format'), value)),
                                 int(abs(value or 0) / peak * 100)))
            if not data:
                parts.append('<tr><td colspan="3" class="m">%s</td></tr>' % E(env_('No data in this period')))
            parts.append('</tbody></table>')
        for w in (w for w in widgets if w.get('type') == 'statement'):
            parts.append('<h2>%s <span class="nc-sub">%s</span></h2><table><tbody>' % (
                E(str(w.get('label') or '')), E(str(w.get('period') or ''))))
            for line in w.get('rows') or ():
                level = int(line.get('level') or 0)
                pad = 8 + 14 * min(level, 6)
                weight = 'bold' if not level else 'normal'
                side = 'right' if self.env['ncollection.dashboard.pdf'].is_rtl() else 'left'
                parts.append('<tr><td style="padding-%s:%dpx;font-weight:%s">%s</td><td class="n%s">%s</td></tr>' % (
                    side, pad, weight, E(str(line.get('label') or '')),
                    ' neg' if (line.get('value') or 0) < 0 else '',
                    E(formatLang(self.env, line.get('value') or 0.0, currency_obj=self.env.company.currency_id))))
            parts.append('</tbody></table>')
        return self.env['ncollection.dashboard.pdf'].document(env_('Summary dashboard'), [
            self.env.company.currency_id.name or '',
            '%s %s → %s' % (env_('From'), meta['period']['date_from'], meta['period']['date_to']),
            '%s %s' % (env_('Exported by'), meta.get('user_name') or '')], ''.join(parts),
            company=meta.get('company_name') or None)

    @api.model
    def build_pdf(self, payload):
        return self.env['ncollection.dashboard.pdf'].render(self.build_html(payload), self.env._('Summary dashboard'))

    @api.model
    def xlsx_b64(self, payload):                      # symmetry with the report engine's contract (tests)
        return base64.b64encode(self.build_xlsx(payload))
