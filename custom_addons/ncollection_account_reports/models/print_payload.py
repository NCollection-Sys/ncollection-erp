# -*- coding: utf-8 -*-
"""The NCollection print design system, data side.

WHY A SEPARATE LAYER. ``_nc_export_payload`` formats a report ONCE for both the PDF and the XLSX, and
that contract stays untouched. A professional printed document also needs things the spreadsheet does not:
the company letterhead facts, a metadata block, a summary band, rows grouped into titled sections with
their own totals, and column widths. All of that is *presentation of numbers the engine already
computed*, and it is prepared here, in Python, so the QWeb templates only render. No accounting is done
in a template and no report is computed twice (the print layer reuses the rows it was handed).

The templates (``report/nc_print.xml``) consume exactly this structure, so one visual system serves
every report. A report customises its print form by overriding ``_nc_print_sections`` /
``_nc_print_summary``; everything else is shared.
"""
import re
from collections import OrderedDict

from odoo import models
from odoo.tools import formatLang

DEFAULT_BRAND = '#1F5F8F'
_HEX = re.compile(r'^#[0-9a-fA-F]{6}$')
# Column width weights: wkhtmltopdf has no flexbox, so widths are decided here and printed as percentages.
_WEIGHT = {'date': 1.45, 'amount': 1.7, 'percent': 1.0}
_WIDE_TEXT = ('label', 'description', 'name', 'account', 'partner')
# A Latin phrase inside Arabic text: starts at a letter/digit (or a '(' before one), runs over Latin
# punctuation and spaces, and ends on a letter, digit, ')', '%' or '.' - never on a trailing space.
_LATIN_RUN = re.compile(r"(?:\((?=[A-Za-z0-9]))?[A-Za-z0-9](?:[A-Za-z0-9 .,&/_()%:+#@'-]*[A-Za-z0-9)%.])?")


def _mix(hex_color, ratio):
    """``hex_color`` blended with white by ``ratio`` (0 = the colour, 1 = white)."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return '#%02X%02X%02X' % tuple(int(round(c + (255 - c) * ratio)) for c in (r, g, b))


class NcPrintPayload(models.AbstractModel):
    _inherit = 'ncollection.account.report'

    # ------------------------------------------------------------------ brand, company, meta
    def _nc_print_brand(self):
        company = self.company_id
        color = ''
        if 'nc_primary_color' in company._fields:      # ncollection_branding is optional here
            color = company.nc_primary_color or ''
        color = color if _HEX.fullmatch(color or '') else DEFAULT_BRAND
        return {'color': color, 'tint': _mix(color, 0.92), 'line': _mix(color, 0.72)}

    def _nc_print_company(self):
        company = self.company_id
        partner = company.partner_id
        country = company.country_id
        city = ' '.join(x for x in (partner.zip, partner.city, partner.state_id.name) if x)
        # No country line: a printed header names the company and its address, and the country alone (Odoo seeds the
        # main company as United States) printed under the name was noise at best and wrong at worst.
        lines = [x for x in (partner.street, partner.street2, city) if x]
        vat_label = country.vat_label or ('TRN' if country.code == 'AE' else self.env._("Tax ID"))
        return {
            'name': company.name or '',
            'lines': lines,
            'contacts': [x for x in (partner.phone, partner.email, partner.website) if x],
            'vat': {'label': vat_label, 'value': company.vat} if company.vat else False,
            'registry': company.company_registry or '',
        }

    @staticmethod
    def _nc_ltr(text):
        """``text`` as one left-to-right run, so a date or a time keeps its order on a right-to-left page."""
        return '‪%s‬' % text if text else ''

    @staticmethod
    def _nc_bidi(text):
        """Each Latin phrase in ``text`` as its own left-to-right run. On a right-to-left page the bidi algorithm
        otherwise flips the brackets and moves the numbers of \"Trading LLC (12)\" or \"(50%)\" when they sit
        beside Arabic words. Pure presentation: the stored value is untouched."""
        if not text or '‪' in text:           # empty, or already an explicit left-to-right run
            return text
        return _LATIN_RUN.sub(lambda m: '‪%s‬' % m.group(0), text)

    def _nc_bidi_payload(self, payload):
        """Apply ``_nc_bidi`` to every free-text string a right-to-left page prints (not numbers or dates)."""
        bidi = self._nc_bidi
        co = payload['co']
        co['name'] = bidi(co['name'])
        co['lines'] = [bidi(x) for x in co['lines']]
        co['contacts'] = [bidi(x) for x in co['contacts']]
        co['registry'] = bidi(co['registry'])
        if co['vat']:
            co['vat'] = dict(co['vat'], value=bidi(co['vat']['value']))
        for doc in payload.get('docs', []):
            party = doc['party']
            party['name'] = bidi(party['name'])
            party['lines'] = [bidi(x) for x in party['lines']]
            party['code'] = bidi(party['code'])
            if party['vat']:
                party['vat'] = dict(party['vat'], value=bidi(party['vat']['value']))
        for sec in payload.get('sections', []) + [s for d in payload.get('docs', []) for s in d['sections']]:
            sec['title'] = bidi(sec['title'])
            for row in sec['rows']:
                for cell in row['cells']:
                    if not cell['numeric'] and cell['cls'] != 'dt':
                        cell['text'] = bidi(cell['text'])
        payload['meta'] = [(label, bidi(text)) for label, text in payload['meta']]
        return payload

    @staticmethod
    def _nc_period(payload):
        """The period as one left-to-right run, "from → to". In a right-to-left page the dates and the arrow are
        all 'strong-ish', so without the embedding the bidi algorithm prints the END date first."""
        if not (payload['date_from'] or payload['date_to']):
            return ''
        return NcPrintPayload._nc_ltr('%s → %s' % (payload['date_from'], payload['date_to']))

    def _nc_print_meta(self, payload):
        """[(label, text)] shown beside the title: period, currency, scope, criteria, who and when."""
        _ = self.env._
        meta = []
        if self._nc_period(payload):
            meta.append((_("Period"), self._nc_period(payload)))
        meta.append((_("Currency"), payload['currency']))
        if payload['entries']:
            meta.append((_("Entries"), payload['entries']))
        meta.extend((label, text) for label, text in payload['filters'])
        meta.append((_("Generated"), payload['generated']))
        meta.append((_("Generated by"), payload['user']))
        return meta

    # ------------------------------------------------------------------ number helpers
    def _nc_amount(self, value, decimals=None):
        if decimals is None:
            decimals = self.company_id.currency_id.decimal_places
        return formatLang(self.env, value or 0.0, digits=decimals)

    @staticmethod
    def _nc_cell(text, numeric=False, value=None, blank_zero=False):
        """One printed cell. ``zero`` / ``neg`` are CSS hooks: zero is muted, a negative keeps its sign."""
        cls = ['num'] if numeric else []
        if numeric and value is not None:
            if not value:
                cls.append('zero')
                if blank_zero:
                    text = ''
            elif value < 0:
                cls.append('neg')
        return {'text': text, 'cls': ' '.join(cls), 'numeric': numeric}

    def _nc_print_cells(self, columns, payload_row):
        """The engine's own formatted cells, as print cells."""
        out = []
        for col, cell in zip(columns, payload_row['cells']):
            numeric = col['numeric']
            value = cell['value'] if numeric and isinstance(cell['value'], (int, float)) else None
            printed = self._nc_cell(cell['text'], numeric, value)
            if col['kind'] == 'date':
                printed['cls'] = 'dt'           # a date never wraps
            out.append(printed)
        return out

    # ------------------------------------------------------------------ sections and summary
    def _nc_print_sections(self, payload, lines):
        """Default: one section, the engine rows, styled by their ``level``."""
        rows = []
        top = [i for i, r in enumerate(payload['rows']) if not r['level']]
        grand = top[-1] if top else -1
        for i, row in enumerate(payload['rows']):
            if row['opening']:
                cls = 'opening'
            elif not row['level']:
                cls = 'grand' if i == grand and len(top) > 1 else 'section'
            else:
                cls = 'l%d' % min(row['level'], 3)
            cells = self._nc_print_cells(payload['columns'], row)
            if cls == 'section':
                # a heading (ASSETS, INCOME ...) carries no figure of its own: a muted 0.00 beside it
                # reads as a balance, so a zero cell is left blank (the grand total row keeps its figures)
                for cell in cells:
                    if cell['numeric'] and 'zero' in cell['cls']:
                        cell['text'] = ''
            rows.append({'cls': cls, 'cells': cells})
        return [{'title': '', 'subtitle': '', 'rows': rows}]

    def _nc_print_summary(self, payload, lines, sections):
        """[(label, text, tone)] for the summary band; none by default."""
        return []

    def _nc_print_widths(self, columns):
        """Percent widths (they sum to 100): amounts and dates are fixed-ish, one wide text column takes the rest."""
        weights = []
        for col in columns:
            kind = col['kind']
            if kind in _WEIGHT:
                weights.append(_WEIGHT[kind])
            else:
                weights.append(4.2 if any(k in col['key'] for k in _WIDE_TEXT) else 2.2)
        total = sum(weights) or 1.0
        pct = [round(100.0 * w / total, 2) for w in weights]
        pct[-1] = round(100.0 - sum(pct[:-1]), 2)
        return pct

    def _nc_group_sections(self, payload, lines, key, title_of, total_label):
        """Rows grouped into titled sections (per account for a ledger, per partner for a partner ledger),
        each with its opening row, its movements and a total row. Totals are sums of the SAME rows and the
        closing is the ledger's own last running balance, never a second computation."""
        _ = self.env._
        columns = payload['columns']
        groups = OrderedDict()
        for line, row in zip(lines, payload['rows']):
            groups.setdefault(line.get(key), []).append((line, row))
        sections = []
        for ident, items in groups.items():
            moves = [(ln, rw) for ln, rw in items if not ln.get('is_initial')]
            opening = next((ln for ln, _rw in items if ln.get('is_initial')), None)
            opening_value = opening['running_balance'] if opening else 0.0
            if not moves and not opening_value:
                continue                                  # nothing to say about this account / partner
            rows = []
            for ln, rw in items:
                cells = self._nc_print_cells(columns, rw)
                for col, cell in zip(columns, cells):
                    if col['key'] in ('debit', 'credit') and cell['cls'].find('zero') >= 0:
                        cell['text'] = ''
                if ln.get('is_initial'):
                    cells = [self._nc_opening_cell(col, cell) for col, cell in zip(columns, cells)]
                    rows.append({'cls': 'opening', 'cells': cells})
                else:
                    rows.append({'cls': '', 'cells': cells})
            rounding = self.company_id.currency_id.round
            debit = rounding(sum(ln['debit'] for ln, _rw in moves))
            credit = rounding(sum(ln['credit'] for ln, _rw in moves))
            closing = items[-1][0]['running_balance']
            rows.append({'cls': 'sub', 'cells': self._nc_total_cells(
                columns, total_label, {'debit': debit, 'credit': credit, 'running_balance': closing})})
            sections.append({'title': title_of(ident), 'subtitle': '', 'rows': rows,
                             'debit': debit, 'credit': credit, 'opening': opening_value, 'closing': closing})
        return sections

    def _nc_opening_cell(self, col, cell):
        """The ledger's opening row: its label column says "Opening balance" (the section title already
        names the account), balance stays, everything else is blank."""
        if col['key'] in ('label', 'description'):
            return self._nc_cell(self.env._("Opening balance"))
        if col['key'] == 'running_balance':
            return cell
        return self._nc_cell('', col['numeric'])

    def _nc_total_cells(self, columns, label, values):
        cells, labelled = [], False
        for col in columns:
            if col['key'] in values:
                v = values[col['key']]
                cells.append(self._nc_cell(self._nc_amount(v), True, v))
            elif not labelled and not col['numeric']:
                cells.append(self._nc_cell(label))
                labelled = True
            else:
                cells.append(self._nc_cell('', col['numeric']))
        return cells

    # ------------------------------------------------------------------ the payload the templates read
    def _nc_print_payload(self):
        """``_nc_export_payload`` plus everything a designed page needs. One call per document."""
        self.ensure_one()
        lines = self._nc_compute_lines()
        payload = self._nc_export_payload(lines)
        payload['generated'] = self._nc_ltr(payload['generated'])
        sections = self._nc_print_sections(payload, lines)
        payload.update(
            rtl=payload['direction'] == 'rtl',
            brand=self._nc_print_brand(),
            co=self._nc_print_company(),
            meta=self._nc_print_meta(payload),
            period=self._nc_period(payload),
            sections=sections,
            summary=self._nc_print_summary(payload, lines, sections),
            widths=self._nc_print_widths(payload['columns']),
        )
        return self._nc_bidi_payload(payload) if payload['rtl'] else payload
