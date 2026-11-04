# -*- coding: utf-8 -*-
"""Excel and PDF files of a Spreadsheet Dashboard (the Dashboards page, ``/atlas/dashboards``).

The figures on that page are computed in the browser by the spreadsheet engine from data the user was allowed to read.
The browser sends back a SNAPSHOT of exactly what it shows - the evaluated cells (a number stays a number, with the
text as displayed beside it) and the figure values (scorecards, charts) - and the server builds BOTH files from
that one snapshot, so the Excel and the PDF cannot disagree with each other or with the screen.

The snapshot is untrusted input, so it is validated and bounded, and the files are built here (never a client-supplied
archive passed through): text is written as text and can never become a formula in a spreadsheet application.
"""
import io
import math
import re
from html import escape

import xlsxwriter

from odoo import api, models
from odoo.exceptions import ValidationError

_MAX_SHEETS, _MAX_ROWS, _MAX_COLS = 30, 1000, 60
_MAX_FIGURES, _MAX_LINES, _MAX_TEXT = 80, 400, 1000
_MAX_CELLS = 200000          # all sheets together: a file this server builds in memory has a ceiling
_FORMULA_STARTS = ('=', '+', '-', '@', '\t', '\r', '\n')
_SHEET_FORBIDDEN = re.compile(r'[\[\]:*?/\\]')
_FILE_UNSAFE = re.compile(r'[^\w-]+')        # letters of any script, digits, _ and - : never a path or a header


def safe_text(value):
    """Text for a spreadsheet cell: never something a spreadsheet application would evaluate as a formula."""
    text = '' if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_STARTS) else text


def _text(value, limit=_MAX_TEXT):
    return ('' if value is None else str(value))[:limit]


class SpreadsheetDashboardExport(models.AbstractModel):
    _name = 'ncollection.spreadsheet.dashboard.export'
    _description = 'NCollection Spreadsheet Dashboard Export'

    # ------------------------------------------------------------------ validation
    @api.model
    def clean_snapshot(self, raw):
        """The snapshot the browser sent, reduced to what is allowed: bounded, typed, text only where text belongs.

        Raises ``ValidationError`` for anything that is not the documented shape - never a partial guess."""
        if not isinstance(raw, dict):
            raise ValidationError(self.env._("The dashboard snapshot is not valid."))
        sheets, cell_count = [], 0
        for sheet in list(raw.get('sheets') or ())[:_MAX_SHEETS]:
            rows = []
            for row in list(sheet.get('rows') or ())[:_MAX_ROWS]:
                cells = []
                for cell in list(row or ())[:_MAX_COLS]:
                    cell_count += 1
                    if cell_count > _MAX_CELLS:
                        raise ValidationError(self.env._("The dashboard is too large to export."))
                    cells.append(self._clean_cell(cell))
                if any(cells):
                    rows.append(cells)
            if rows:
                sheets.append({'name': _text(sheet.get('name'), 80) or self.env._("Sheet"), 'rows': rows})
        figures = []
        for figure in list(raw.get('figures') or ())[:_MAX_FIGURES]:
            lines = [[_text(part, 200) for part in list(line)[:_MAX_COLS]]
                     for line in list(figure.get('lines') or ())[:_MAX_LINES]]
            figures.append({'title': _text(figure.get('title'), 200), 'lines': lines})
        return {
            'name': _text(raw.get('name'), 120),
            'filters': [_text(item, 200) for item in list(raw.get('filters') or ())[:30]],
            'sheets': sheets, 'figures': figures,
        }

    @api.model
    def _clean_cell(self, cell):
        """``None`` or ``(kind, value, shown)`` with kind ``'n'`` (a finite number) or ``'s'`` (text)."""
        if not cell:
            return None
        kind, value, shown = (list(cell) + [None, None, None])[:3]
        if kind == 'n':
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValidationError(self.env._("The dashboard snapshot is not valid."))
            return ('n', float(value), _text(shown, 100))
        if kind == 's':
            return ('s', _text(value), _text(shown))
        raise ValidationError(self.env._("The dashboard snapshot is not valid."))

    # ------------------------------------------------------------------ names
    @api.model
    def is_rtl(self):
        lang = self.env['res.lang']._lang_get(self.env.lang or self.env.user.lang)
        return bool(lang and lang.direction == 'rtl')

    @api.model
    def export_filename(self, name, extension):
        stem = 'Dashboard-%s' % (_FILE_UNSAFE.sub('-', name or 'dashboard').strip('-') or 'dashboard')
        return '%s.%s' % (stem[:100], extension)

    # ------------------------------------------------------------------ Excel
    @api.model
    def build_xlsx(self, snapshot):
        buffer = io.BytesIO()
        # strings are never read as formulas, links or numbers by the writer itself
        workbook = xlsxwriter.Workbook(buffer, {
            'in_memory': True, 'strings_to_formulas': False, 'strings_to_urls': False,
            'strings_to_numbers': False})
        rtl = self.is_rtl()
        bold = workbook.add_format({'bold': True, 'bg_color': '#EEF2F7', 'border': 1})
        used = set()

        def sheet(title):
            base = (_SHEET_FORBIDDEN.sub(' ', title).strip() or 'Sheet')[:28]
            candidate, n = base, 1
            while candidate.lower() in used:
                n += 1
                candidate = '%s %d' % (base[:25], n)
            used.add(candidate.lower())
            ws = workbook.add_worksheet(candidate)
            if rtl:
                ws.right_to_left()
            return ws

        summary = sheet(self.env._("Summary"))
        summary.write_string(0, 0, safe_text(snapshot['name'] or self.env._("Dashboard")), bold)
        for index, text in enumerate(snapshot['filters'], start=1):
            summary.write_string(index, 0, safe_text(text))
        summary.set_column(0, 0, 60)

        for data in snapshot['sheets']:
            ws = sheet(data['name'])
            for r, row in enumerate(data['rows']):
                for c, cell in enumerate(row):
                    if not cell:
                        continue
                    kind, value, _shown = cell
                    if kind == 'n':
                        ws.write_number(r, c, value)
                    else:
                        ws.write_string(r, c, safe_text(value))
            ws.set_column(0, min(_MAX_COLS, 25), 18)

        if snapshot['figures']:
            ws = sheet(self.env._("Figures"))
            r = 0
            for figure in snapshot['figures']:
                ws.write_string(r, 0, safe_text(figure['title']), bold)
                r += 1
                for line in figure['lines']:
                    for c, part in enumerate(line):
                        ws.write_string(r, c, safe_text(part))
                    r += 1
                r += 1
            ws.set_column(0, 0, 40)
        workbook.close()
        return buffer.getvalue()

    # ------------------------------------------------------------------ PDF
    @api.model
    def build_html(self, snapshot):
        rtl = self.is_rtl()
        direction, align, other = ('rtl', 'right', 'left') if rtl else ('ltr', 'left', 'right')
        E = escape
        parts = [
            '<!DOCTYPE html><html dir="%s" lang="%s"><head><meta charset="utf-8"/><style>' % (
                direction, E(self.env.lang or 'en_US')),
            'body{font-family:"Noto Sans Arabic","Noto Sans","DejaVu Sans",Arial,sans-serif;font-size:12px;'
            'color:#1f2933;direction:%s;margin:0}' % direction,
            'h1{font-size:20px;margin:0 0 4px}h2{font-size:14px;margin:18px 0 6px;border-bottom:1px solid #cbd2d9;'
            'padding-bottom:3px}.ctx{color:#52606d;margin-bottom:10px}',
            'table{width:100%%;border-collapse:collapse;page-break-inside:auto}tr{page-break-inside:avoid}'
            'th,td{border:1px solid #d9e2ec;padding:3px 6px;text-align:%s}td.n{text-align:%s;white-space:nowrap}'
            % (align, other),
            '</style></head><body>',
            '<h1>%s</h1>' % E(snapshot['name'] or self.env._("Dashboard")),
        ]
        if snapshot['filters']:
            parts.append('<div class="ctx">%s</div>' % ' &middot; '.join(E(item) for item in snapshot['filters']))
        for figure in snapshot['figures']:
            parts.append('<h2>%s</h2><table>' % E(figure['title']))
            for line in figure['lines']:
                parts.append('<tr>%s</tr>' % ''.join(
                    '<%s>%s</%s>' % ('td' if i else 'th', E(part), 'td' if i else 'th')
                    for i, part in enumerate(line)))
            parts.append('</table>')
        for data in snapshot['sheets']:
            parts.append('<h2>%s</h2><table>' % E(data['name']))
            for row in data['rows']:
                parts.append('<tr>%s</tr>' % ''.join(
                    '<td class="%s">%s</td>' % ('n' if cell and cell[0] == 'n' else '',
                                                E(cell[2] or str(cell[1])) if cell else '')
                    for cell in row))
            parts.append('</table>')
        parts.append('</body></html>')
        return ''.join(parts)

    @api.model
    def build_pdf(self, snapshot):
        return self.env['ir.actions.report']._run_wkhtmltopdf(
            [self.build_html(snapshot)], footer=None, landscape=False, set_viewport_size=True)
