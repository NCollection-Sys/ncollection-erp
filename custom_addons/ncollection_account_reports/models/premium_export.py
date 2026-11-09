# -*- coding: utf-8 -*-
# flake8: noqa: E501  (user-facing definitions are single translatable sentences)
"""Excel and PDF of the NCollection Sales / Invoicing dashboards.

Unlike the Spreadsheet Dashboards export (a snapshot the browser sends), these files are built from the SERVER payload
(``ncollection.premium.dashboard``): nothing a client says is trusted, so they are server-verified figures of the active
company, in its currency, with the same definitions as the screen. Text can never become a formula in a spreadsheet.
"""
import io
import re
from html import escape

import xlsxwriter

from odoo import api, models

from odoo.addons.ncollection_core.models.dashboard.dashboard_export import safe_text

_FILE_UNSAFE = re.compile(r'[^\w-]+')


class NcollectionPremiumExport(models.AbstractModel):
    _name = 'ncollection.premium.dashboard.export'
    _description = 'NCollection Sales / Invoicing Dashboard Export'

    @api.model
    def _title(self, kind):
        return self.env._("Sales Analysis") if kind == 'sales' else self.env._("Invoicing Analysis")

    @api.model
    def _is_rtl(self):
        lang = self.env['res.lang']._lang_get(self.env.lang or self.env.user.lang)
        return bool(lang and lang.direction == 'rtl')

    @api.model
    def filename(self, kind, payload, ext):
        meta = payload['meta']
        company = _FILE_UNSAFE.sub('-', meta['company']).strip('-') or 'company'
        stem = 'NCollection-%s-%s-%s_%s' % (kind.capitalize(), company, meta['period']['from'], meta['period']['to'])
        return '%s.%s' % (stem[:120], ext)

    @api.model
    def build_xlsx(self, kind, payload):
        meta, env = payload['meta'], self.env
        buffer = io.BytesIO()
        book = xlsxwriter.Workbook(buffer, {'in_memory': True, 'strings_to_formulas': False, 'strings_to_urls': False,
                                            'strings_to_numbers': False})
        bold = book.add_format({'bold': True, 'bg_color': '#EEF2F7', 'border': 1})
        money = book.add_format({'num_format': '#,##0.%s' % ('0' * meta['currency']['decimals'])})
        formats = {'currency': money, 'percent': book.add_format({'num_format': '0.0"%"'}),
                   'days': book.add_format({'num_format': '0.0'}), 'count': None}
        unit = {'currency': meta['currency']['name'], 'percent': '%', 'days': env._("days"), 'count': ''}
        sheet = book.add_worksheet(safe_text(self._title(kind))[:28])
        if self._is_rtl():
            sheet.right_to_left()
        sheet.set_column(0, 0, 38)
        sheet.set_column(1, 3, 22)
        sheet.set_column(4, 4, 70)
        row = 0
        sheet.write_string(row, 0, safe_text(self._title(kind)), bold)
        for label, value in (
                (env._("Company"), meta['company']),
                (env._("Currency"), '%s (%s)' % (meta['currency']['name'], meta['currency']['symbol'])),
                (env._("Period"), '%s → %s' % (meta['period']['from'], meta['period']['to'])),
                (env._("Basis"), env._("Calculated by the server from the company's records; not a client snapshot."))):
            row += 1
            sheet.write_string(row, 0, safe_text(label))
            sheet.write_string(row, 1, safe_text(value))
        row += 2
        for col, head in enumerate((env._("Indicator"), env._("Value"), env._("Previous period"), env._("Unit"),
                                    env._("Definition"))):
            sheet.write_string(row, col, safe_text(head), bold)
        for kpi in payload['kpis']:
            row += 1
            sheet.write_string(row, 0, safe_text(kpi['label']))
            for col, value in ((1, kpi['value']), (2, kpi['previous'])):
                if value is None:
                    sheet.write_string(row, col, safe_text(env._("n/a")))
                else:
                    sheet.write_number(row, col, float(value), formats[kpi['kind']])
            sheet.write_string(row, 3, safe_text(unit[kpi['kind']]))
            sheet.write_string(row, 4, safe_text(kpi['tip'] + ((' ' + kpi['note']) if kpi.get('note') else '')))
        for table in payload['tables']:
            row += 2
            sheet.write_string(row, 0, safe_text(table['label']), bold)
            if not table['rows']:
                row += 1
                sheet.write_string(row, 0, safe_text(table['empty']))
            for item in table['rows']:
                row += 1
                sheet.write_string(row, 0, safe_text(item.get('label') or item.get('name')))
                sheet.write_string(row, 1, safe_text(item.get('partner') or ''))
                sheet.write_string(row, 2, safe_text(item.get('date') or ''))
                sheet.write_number(row, 3, float(item['value']), money)
        book.close()
        return buffer.getvalue()

    @api.model
    def build_html(self, kind, payload):
        meta, env, E = payload['meta'], self.env, escape
        decimals = meta['currency']['decimals']

        def shown(kpi):
            value = kpi['value']
            if value is None:
                return E(env._("n/a"))
            if kpi['kind'] == 'currency':
                return E('%s %s' % (format(value, ',.%df' % decimals), meta['currency']['name']))
            if kpi['kind'] == 'percent':
                return '%.1f%%' % value
            if kpi['kind'] == 'days':
                return '%.1f %s' % (value, E(env._("days")))
            return '%d' % value

        parts = [
            '<p class="nc-note">%s</p>' % E(env._("Calculated by the server from the company's records.")),
            '<h2>%s</h2><table class="nc-kpi"><thead><tr><th>%s</th><th class="n">%s</th><th>%s</th></tr></thead><tbody>' % (
                E(env._("Key figures")), E(env._("Indicator")), E(env._("Value")), E(env._("Definition")))]
        for kpi in payload['kpis']:
            note = ' ' + kpi['note'] if kpi.get('note') else ''
            parts.append('<tr><td>%s</td><td class="n">%s</td><td class="m">%s</td></tr>' % (
                E(kpi['label']), shown(kpi), E(kpi['tip'] + note)))
        parts.append('</tbody></table>')
        for table in payload['tables']:
            parts.append('<h2>%s</h2><table><tbody>' % E(table['label']))
            if not table['rows']:
                parts.append('<tr><td class="m">%s</td></tr>' % E(table['empty']))
            for item in table['rows']:
                parts.append('<tr><td dir="auto">%s</td><td dir="auto">%s</td><td>%s</td><td class="n">%s</td></tr>' % (
                    E(str(item.get('label') or item.get('name'))), E(item.get('partner') or ''),
                    E(item.get('date') or ''), E(format(item['value'], ',.%df' % decimals))))
            parts.append('</tbody></table>')
        return env['ncollection.dashboard.pdf'].document(self._title(kind), [
            '%s (%s)' % (meta['currency']['name'], meta['currency']['symbol']),
            '%s → %s' % (meta['period']['from'], meta['period']['to'])], ''.join(parts), company=meta['company'])

    @api.model
    def build_pdf(self, kind, payload):
        return self.env['ncollection.dashboard.pdf'].render(self.build_html(kind, payload), self._title(kind))
