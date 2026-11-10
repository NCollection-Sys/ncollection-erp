# -*- coding: utf-8 -*-
"""The ONE page design of every Dashboard PDF (Sales, Invoicing, the Summary dashboard, the Spreadsheet Dashboards).

WHY A DEDICATED PAPER FORMAT. ``ir.actions.report._run_wkhtmltopdf`` called without a report takes the COMPANY's paper
format - the one made for a letterhead document, with a tall top margin and thin sides. A dashboard sent through it
started
half way down the page, hard against the left edge. These exports pass ``action_nc_dashboard_pdf`` instead, whose paper
format is A4 with real margins on every side (``data/dashboard_pdf.xml``), and a footer with the page number.

The builders (account_reports, core, dashboard_export) only produce the BODY; the design lives here so the three
cannot drift.
Figures are never re-computed here: this module lays out what the caller already calculated.
"""

import re
from html import escape

from odoo import api, models

# "4,901AED" -> "4,901 AED": a figure the spreadsheet engine formatted with the currency code glued to the digits.
_GLUED_CODE = re.compile(r'^(-?[\d.,]*\d)([A-Za-z]{3})$')
_GLUED_PREFIX = re.compile(r'^([A-Za-z]{3})(-?\d[\d.,]*)$')

_CSS = """
html, body { margin: 0; padding: 0; }
body { font-family: "Noto Sans Arabic","Noto Sans","DejaVu Sans",Arial,sans-serif; font-size: 9.5pt; line-height: 1.45;
       color: #1f2933; direction: %(dir)s; }
.nc-head { border-bottom: 2px solid #1d4f7c; padding-bottom: 6px; margin-bottom: 12px; }
.nc-company { font-size: 8.5pt; letter-spacing: .04em; color: #52606d; text-transform: uppercase; }
h1 { font-size: 17pt; margin: 2px 0 4px; color: #102a43; font-weight: 700; }
.nc-ctx { color: #52606d; font-size: 8.5pt; }
.nc-ctx span + span::before { content: "\\00a0\\00b7\\00a0"; }
h2 { font-size: 11.5pt; margin: 16px 0 6px; padding-bottom: 3px; border-bottom: 1px solid #cbd2d9; color: #102a43;
     page-break-after: avoid; break-after: avoid; }
.nc-sub { color: #7b8794; font-size: 8pt; font-weight: 400; }
.nc-note { color: #52606d; font-size: 8.5pt; margin: 2px 0 8px; }
table { width: 100%%; border-collapse: collapse; table-layout: auto; page-break-inside: auto; }
thead { display: table-header-group; }
tr { page-break-inside: avoid; break-inside: avoid; }
th, td { border: 1px solid #d9e2ec; padding: 4px 8px; text-align: %(start)s; vertical-align: top;
         word-wrap: break-word; overflow-wrap: anywhere; }
th { background: #eef2f7; font-weight: 600; color: #243b53; }
tbody tr:nth-child(even) td { background: #f8fafc; }
.nc-kpi th:nth-child(1) { width: 27%%; } .nc-kpi th:nth-child(2) { width: 17%%; }
[dir=auto] { unicode-bidi: plaintext; }
td.n, th.n { text-align: %(end)s; white-space: nowrap; direction: ltr; unicode-bidi: embed; }
td.m { color: #52606d; font-size: 8.5pt; }
.neg { color: #b91c1c; }
.bar { background: #3b82f6; height: 7px; display: inline-block; }
"""

_FOOTER = """<!DOCTYPE html><html dir="%(dir)s"><head><meta charset="utf-8"/><script>
function subst() { var vars = {}; var q = document.location.search.substring(1).split('&');
  for (var i in q) { var z = q[i].split('=', 2); vars[z[0]] = decodeURIComponent(z[1]); }
  ['page', 'topage'].forEach(function (c) { var e = document.getElementsByClassName(c);
    for (var j = 0; j < e.length; ++j) e[j].textContent = vars[c]; }); }
</script><style>
body { margin: 0; font-family: "Noto Sans Arabic","Noto Sans","DejaVu Sans",Arial,sans-serif; font-size: 8pt; color:
#7b8794; }
.f { border-top: 1px solid #cbd2d9; padding-top: 4px; display: table; width: 100%%; }
.f > span { display: table-cell; } .f > span:first-child { max-width: 120mm; overflow: hidden; text-overflow:
ellipsis; white-space: nowrap; }
.f > span + span { text-align: %(end)s; white-space: nowrap; }
</style></head><body onload="subst()">
<div class="f"><span>%(left)s</span>
<span>%(page)s <span dir="ltr"
style="unicode-bidi:isolate;display:inline-block">
<span class="page"></span> / <span class="topage"></span></span></span></div>
</body></html>"""


class DashboardPdf(models.AbstractModel):
    _name = 'ncollection.dashboard.pdf'
    _description = 'Dashboard PDF page design'

    @api.model
    def is_rtl(self):
        lang = self.env['res.lang']._lang_get(self.env.lang or self.env.user.lang)
        return bool(lang and lang.direction == 'rtl')

    @api.model
    def tidy_amount(self, text):
        """Put a space between a figure and a glued currency code (display only; the Excel file keeps the cell as
        is)."""
        if not isinstance(text, str):
            return text
        match = _GLUED_CODE.fullmatch(text.strip())
        if match:
            return '%s %s' % (match.group(1), match.group(2))
        match = _GLUED_PREFIX.fullmatch(text.strip())
        if match and match.group(1).isupper():
            return '%s %s' % (match.group(2), match.group(1))
        return text

    @api.model
    def document(self, title, context_parts, body, company=None):
        """A complete HTML document: header (company, title, context line), then ``body`` (already escaped HTML)."""
        rtl = self.is_rtl()
        css = _CSS % {
            'dir': 'rtl' if rtl else 'ltr',
            'start': 'right' if rtl else 'left',
            'end': 'left' if rtl else 'right',
        }
        context = ''.join('<span>%s</span>' % escape(str(part)) for part in context_parts if part)
        return (
            '<!DOCTYPE html><html dir="%s" lang="%s"><head><meta charset="utf-8"/><style>%s</style></head><body>'
            '<div class="nc-head"><div class="nc-company">%s</div><h1>%s</h1><div class="nc-ctx">%s</div></div>%s'
            '</body></html>'
        ) % (
            'rtl' if rtl else 'ltr',
            escape(self.env.lang or 'en_US'),
            css,
            escape(company or self.env.company.name or ''),
            escape(title or ''),
            context,
            body,
        )

    @api.model
    def render(self, html, title='', landscape=False):
        """The PDF of ``html`` on the dashboard paper format, with a footer (company, title, page n / N)."""
        rtl = self.is_rtl()
        footer = _FOOTER % {
            'dir': 'rtl' if rtl else 'ltr',
            'end': 'left' if rtl else 'right',
            'left': escape('%s · %s' % (self.env.company.name or '', title or '')),
            'page': escape(self.env._("Page")),
        }
        return self.env['ir.actions.report']._run_wkhtmltopdf(
            [html], report_ref='ncollection_branding.action_nc_dashboard_pdf', footer=footer, landscape=landscape
        )
