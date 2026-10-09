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

from odoo import api, fields, models
from odoo.tools.misc import format_date, formatLang
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

    def _nc_filter_domain(self, account=None, partner=None):
        """Common NON-date journal-item filters (company, posted/all, journals,
        accounts, partners). Date range is added by the caller — the period,
        opening and closing aggregates each need a different one."""
        self.ensure_one()
        domain = [
            ('company_id', '=', self.company_id.id),
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

    def _nc_move_line_domain(self, account=None, partner=None):
        """The PERIOD journal-item domain: the common filters + [date_from,
        date_to]. Pass ``account``/``partner`` to scope it (drill-down)."""
        return self._nc_filter_domain(account=account, partner=partner) + [
            ('date', '>=', self.date_from), ('date', '<=', self.date_to)]

    def _nc_assert_single_fiscal_year(self):
        """A report period must lie within ONE fiscal year. The opening logic
        resets P&L accounts at the fiscal-year start, which is only well-defined
        for a single-FY period; spanning a boundary would silently mix two years'
        P&L activity. For a financial report a hard error beats a wrong number."""
        self.ensure_one()
        fy_from = self.company_id.compute_fiscalyear_dates(self.date_from)['date_from']
        fy_to = self.company_id.compute_fiscalyear_dates(self.date_to)['date_from']
        if fy_from != fy_to:
            raise UserError(self.env._(
                "The report period must fall within a single fiscal year — the "
                "From/To range crosses a fiscal-year boundary. Run one report "
                "per fiscal year."))

    def _nc_opening_balances(self):
        """``{account_id: opening balance at date_from}`` — the accounting-correct
        opening used by BOTH the Trial Balance and the General Ledger.

        Accounts are partitioned by Odoo's OWN carry-forward flag
        (``account.account.include_initial_balance``): it is True for
        balance-sheet accounts, which carry ALL prior activity, and False for
        P&L income/expense accounts AND the auto current-year-earnings account
        (``equity_unaffected``), which reset at the fiscal-year start. We read
        that flag directly rather than hand-maintain an ``account_type``
        allowlist — Odoo owns the classification (FPA §6), so future
        localisation types (e.g. ``expense_other``) are handled for free.
        Assumes a single-fiscal-year period (guarded above)."""
        self.ensure_one()
        self._nc_assert_single_fiscal_year()
        AML = self.env['account.move.line']
        base = self._nc_filter_domain()
        fy_from = self.company_id.compute_fiscalyear_dates(self.date_from)['date_from']
        # active_test=False: an ARCHIVED account can still carry a balance —
        # dropping it here would silently zero its opening (its move lines still
        # surface via the period query, so the row would show a wrong opening).
        Account = self.env['account.account'].with_context(active_test=False)
        accounts = Account.search(Account._check_company_domain(self.company_id))
        carry = accounts.filtered('include_initial_balance')   # balance-sheet
        reset = accounts - carry                               # P&L + current-year earnings
        opening = {}
        # Balance-sheet accounts: all activity before the report start date.
        for account, bal in AML._read_group(
                base + [('date', '<', self.date_from),
                        ('account_id', 'in', carry.ids)],
                groupby=['account_id'], aggregates=['balance:sum']):
            if account:
                opening[account.id] = bal
        # P&L + current-year-earnings: only the current fiscal year to date.
        for account, bal in AML._read_group(
                base + [('date', '>=', fy_from), ('date', '<', self.date_from),
                        ('account_id', 'in', reset.ids)],
                groupby=['account_id'], aggregates=['balance:sum']):
            if account:
                opening[account.id] = bal
        # Affectation of results: PRIOR fiscal years' net P&L rolls into the
        # current-year-earnings (equity_unaffected) account's opening. Odoo
        # computes this rather than posting it, so without it the opening trial
        # balance would not balance. Mirrors OCA account_financial_report's
        # _get_pl_initial_balance (sum of reset-account balances before fy_from).
        unaffected = accounts.filtered(
            lambda a: a.account_type == 'equity_unaffected')[:1]
        # An explicit account filter means exactly those accounts: the roll-up row
        # is added only when the user did not narrow the list, or asked for it.
        # (Found by the Trial Balance pilot: with an account filter the native
        # report grew a Current Year Earnings row the user had excluded, which the
        # OCA engine - and the totals - did not have.)
        if unaffected and self.account_ids and unaffected not in self.account_ids:
            unaffected = unaffected.browse()
        if unaffected:
            pl_prior = sum(
                bal for _account, bal in AML._read_group(
                    base + [('date', '<', fy_from), ('account_id', 'in', reset.ids)],
                    groupby=['account_id'], aggregates=['balance:sum']))
            if pl_prior:
                opening[unaffected.id] = opening.get(unaffected.id, 0.0) + pl_prior
        return opening

    def _nc_closing_balances(self):
        """``{account_id: cumulative balance at date_to}`` — every posting from
        the beginning of time up to and including the date.

        This is mis_builder's ``bale`` ("balance end") semantics, and it is what
        a **Balance Sheet** needs: a statement of position is cumulative, not a
        period movement. Deliberately does NOT call
        ``_nc_assert_single_fiscal_year`` — that guard exists because the
        *opening* logic resets P&L accounts at the fiscal-year start, which is
        only well-defined inside one FY. A cumulative to-date sum has no such
        reset, so a range spanning a boundary is not ambiguous here.

        Added by F2-T03; ``_nc_opening_balances`` above is untouched and remains
        what the Trial Balance and General Ledger use.
        """
        self.ensure_one()
        AML = self.env['account.move.line']
        domain = self._nc_filter_domain() + [('date', '<=', self.date_to)]
        return {account.id: balance
                for account, balance in AML._read_group(
                    domain, groupby=['account_id'], aggregates=['balance:sum'])
                if account}

    def _nc_period_balances(self):
        """``{account_id: balance MOVEMENT over the period}`` — mis_builder's
        ``balp`` ("balance period"). What a **P&L** needs: a flow, not a
        position. Added by F2-T03.

        Both helpers read the period off ``self``; to evaluate a DIFFERENT
        period use ``_nc_comparison_record()``, which rebinds the dates on an
        in-memory copy so every filter travels with them.
        """
        self.ensure_one()
        AML = self.env['account.move.line']
        domain = self._nc_filter_domain() + [
            ('date', '>=', self.date_from), ('date', '<=', self.date_to)]
        return {account.id: balance
                for account, balance in AML._read_group(
                    domain, groupby=['account_id'], aggregates=['balance:sum'])
                if account}

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
            'opening_balance': row.get('opening_balance', 0.0),
            'closing_balance': row.get('closing_balance', 0.0),
            # F2-T03 comparison columns. Additive: every pre-existing report
            # (GL, Trial Balance) simply omits these keys and gets 0.0, and no
            # list view of theirs references the fields — so their on-screen
            # output is byte-identical to before.
            'current_amount': row.get('current_amount', 0.0),
            'previous_amount': row.get('previous_amount', 0.0),
            'variance': row.get('variance', 0.0),
            'variance_pct': row.get('variance_pct', 0.0),
            'ratio_pct': row.get('ratio_pct', 0.0),
            'level': row.get('level', 0),
            'currency_id': currency_id,
        } for row in self._nc_compute_lines()])
        return {
            'type': 'ir.actions.act_window',
            'name': self._nc_report_title(),
            'res_model': 'ncollection.account.report.line',
            'view_mode': 'list',
            'views': [(self.env.ref(self._nc_list_view_ref()).id, 'list')],
            'domain': [('id', 'in', created.ids)],
            'target': 'current',
        }

    def _nc_list_view_ref(self):
        """Xmlid of the list view for the on-screen results. Reports with extra
        columns (e.g. Trial Balance's opening/closing) override this."""
        return 'ncollection_account_reports.view_report_line_list'

    # ---- PDF (native qweb-pdf) ------------------------------------------

    def _nc_report_action_ref(self):
        """Xmlid of this report's ir.actions.report (all share the generic
        ``report_financial`` template but need a per-model action)."""
        return 'ncollection_account_reports.action_report_financial'

    def action_export_pdf(self):
        self.ensure_one()
        return self.env.ref(self._nc_report_action_ref()).report_action(
            self, config=False)

    # ---- XLSX (xlsxwriter — no OCA dep) ---------------------------------

    def action_export_xlsx(self):
        self.ensure_one()
        if xlsxwriter is None:
            raise UserError(self.env._(
                "XLSX export needs the 'xlsxwriter' Python library."))
        # #250: stream from a controller instead of persisting an ir.attachment.
        # The old path stored the workbook against this TRANSIENT wizard, so
        # once autovacuum reclaimed the wizard the attachment was orphaned with
        # a dangling res_id and nothing collected it — a slow leak across all
        # nine report models. No artefact, no leak, and no stored object whose
        # id could be guessed.
        return {
            'type': 'ir.actions.act_url',
            'url': '/ncollection/account_reports/xlsx/%s/%s' % (self._name, self.id),
            'target': 'self',
        }

    def _nc_sheet_name(self):
        """Excel forbids : \\ / ? * [ ] in sheet names and caps them at 31 chars.
        Every F2-T02+ report inherits this, so sanitise once here."""
        title = self._nc_report_title()
        for ch in ':\\/?*[]':
            title = title.replace(ch, ' ')
        return (title.strip() or 'Report')[:31]

    # ---- one payload for the two print channels (PDF + XLSX) -------------

    _NC_PCT_KEYS = ('variance_pct', 'ratio_pct')

    def _nc_export_kind(self, col):
        """How a column is rendered in print: amount | percent | date | text."""
        if col.get('type') == 'percent' or col['key'] in self._NC_PCT_KEYS:
            return 'percent'
        if col.get('type') == 'monetary':
            return 'amount'
        if col['key'] == 'date':
            return 'date'
        return 'text'

    def _nc_export_filters(self):
        """The active filters a printed report must state, as [(label, text)]."""
        self.ensure_one()
        env = self.env
        out = []
        for name in ('journal_ids', 'account_ids', 'partner_ids'):
            if name in self._fields and self[name]:
                out.append((self._fields[name].get_description(env, ['string'])['string'],
                            ', '.join(self[name].mapped('display_name'))))
        if 'comparison_type' in self._fields and self.comparison_type \
                and self.comparison_type != 'none':
            label = dict(self._fields['comparison_type']._description_selection(env)).get(
                self.comparison_type, '')
            out.append((self._fields['comparison_type'].get_description(env, ['string'])['string'], label))
        for name in ('partner_scope',):
            if name in self._fields and self[name]:
                label = dict(self._fields[name]._description_selection(env)).get(self[name], '')
                out.append((self._fields[name].get_description(env, ['string'])['string'], label))
        return out

    def _nc_export_payload(self):
        """Everything a printed copy needs, formatted ONCE.

        Both the PDF template and the XLSX builder read this, so the two cannot
        disagree and neither formats a number itself. The previous PDF template
        formatted percentages with a Python `%` inside a QWeb attribute; QWeb
        runs its own `%` pass over the compiled expression, which turned
        `'%.2f%%'` into `'%.2f%'` and crashed EVERY report that has a percent
        column ("ValueError: incomplete format").

        Numbers are rendered with the user's language separators and Latin
        digits, with the company currency's decimals; the currency is stated in
        the header rather than repeated in every cell.
        """
        self.ensure_one()
        env = self.env
        currency = self.company_id.currency_id
        lang = env['res.lang']._get_data(code=env.lang or 'en_US')
        columns = []
        for col in self._nc_columns():
            kind = self._nc_export_kind(col)
            columns.append({'key': col['key'], 'label': col['label'], 'kind': kind,
                            'numeric': kind in ('amount', 'percent')})
        rows = []
        for row in self._nc_compute_lines():
            cells = []
            for col in columns:
                value = row.get(col['key'])
                if col['kind'] == 'amount':
                    text = formatLang(env, value or 0.0, digits=currency.decimal_places)
                elif col['kind'] == 'percent':
                    text = formatLang(env, value or 0.0, digits=2) + '%'
                elif col['kind'] == 'date':
                    text = format_date(env, value) if value else ''
                else:
                    text = '' if value in (None, False) else str(value)
                cells.append({'value': value, 'text': text})
            rows.append({'level': row.get('level', 0) or 0,
                         'opening': bool(row.get('is_initial')), 'cells': cells})
        entries = ''
        if 'target_move' in self._fields:
            entries = dict(self._fields['target_move']._description_selection(env)).get(
                self.target_move, '')
        stamp = fields.Datetime.context_timestamp(self, fields.Datetime.now())
        return {
            'title': self._nc_report_title(),
            'company': self.company_id.name,
            'currency': currency.name,
            'decimals': currency.decimal_places,
            'date_from': format_date(env, self.date_from) if self.date_from else '',
            'date_to': format_date(env, self.date_to) if self.date_to else '',
            'entries': entries,
            'filters': self._nc_export_filters(),
            'generated': stamp.strftime('%Y-%m-%d %H:%M'),
            'user': env.user.name,
            'direction': lang.direction or 'ltr',
            'columns': columns,
            'rows': rows,
        }

    def _nc_build_xlsx(self):
        """Render the report to an XLSX workbook (base64 bytes).

        Real numbers (not text) with the currency's decimals, real dates, a
        header block, bold totals, a frozen and repeating header row, widths
        sized to the content, A4 landscape fit-to-width, and a right-to-left
        sheet when the user's language is right-to-left.
        """
        self.ensure_one()
        data = self._nc_export_payload()
        decimals = data['decimals']
        stream = io.BytesIO()
        book = xlsxwriter.Workbook(stream, {'in_memory': True})
        sheet = book.add_worksheet(self._nc_sheet_name())
        if data['direction'] == 'rtl':
            sheet.right_to_left()
        num = '#,##0.%s;-#,##0.%s' % ('0' * decimals, '0' * decimals) if decimals else '#,##0;-#,##0'

        def fmt(**kw):
            return book.add_format(dict({'font_size': 10, 'valign': 'vcenter'}, **kw))

        f_title = fmt(bold=True, font_size=14)
        f_meta = fmt(font_color='#475569')
        f_head = fmt(bold=True, bottom=1, bg_color='#F1F5F9', text_wrap=True)
        f_head_num = fmt(bold=True, bottom=1, bg_color='#F1F5F9', align='right', text_wrap=True)
        styles = {}

        def style(kind, bold, italic):
            key = (kind, bold, italic)
            if key not in styles:
                props = {'bold': bold, 'italic': italic}
                if kind == 'amount':
                    props.update(num_format=num, align='right')
                elif kind == 'percent':
                    props.update(num_format='0.00"%"', align='right')
                elif kind == 'date':
                    props.update(num_format='yyyy-mm-dd', align='left')
                if bold:
                    props.update(top=1)
                styles[key] = fmt(**props)
            return styles[key]

        ncols = len(data['columns'])
        r = 0
        sheet.write(r, 0, data['title'], f_title)
        r += 1
        sheet.write(r, 0, data['company'], f_meta)
        r += 1
        period = ' → '.join(x for x in (data['date_from'], data['date_to']) if x)
        sheet.write(r, 0, '%s    %s' % (period, data['currency']), f_meta)
        if data['entries']:
            r += 1
            sheet.write(r, 0, data['entries'], f_meta)
        for label, text in data['filters']:
            r += 1
            sheet.write(r, 0, '%s: %s' % (label, text), f_meta)
        r += 1
        sheet.write(r, 0, '%s · %s' % (data['generated'], data['user']), f_meta)
        header_row = r + 2
        widths = [len(c['label']) for c in data['columns']]
        for col, cdef in enumerate(data['columns']):
            sheet.write(header_row, col, cdef['label'], f_head_num if cdef['numeric'] else f_head)
        hierarchical = any(r['level'] for r in data['rows'])
        for i, row in enumerate(data['rows']):
            rr = header_row + 1 + i
            bold = hierarchical and row['level'] == 0 and not row['opening']
            italic = row['opening']
            for col, (cdef, cell) in enumerate(zip(data['columns'], row['cells'])):
                value, kind = cell['value'], cdef['kind']
                st = style(kind, bold and kind != 'date', italic)
                if kind in ('amount', 'percent'):
                    sheet.write_number(rr, col, float(value or 0.0), st)
                elif kind == 'date' and value:
                    sheet.write_datetime(rr, col, fields.Datetime.to_datetime(value), st)
                else:
                    sheet.write_string(rr, col, cell['text'], st)
                widths[col] = max(widths[col], min(len(cell['text']), 60))
        for col, width in enumerate(widths):
            sheet.set_column(col, col, min(max(width + 3, 12), 62))
        sheet.freeze_panes(header_row + 1, 1 if ncols > 1 else 0)
        sheet.repeat_rows(header_row)
        sheet.set_landscape()
        sheet.set_paper(9)  # A4
        sheet.fit_to_pages(1, 0)
        sheet.set_footer('&C&P / &N')
        book.close()
        return base64.b64encode(stream.getvalue())

    @api.model
    def _nc_source_ids(self, line):
        """Exact source records behind a journal item, for report drill-down.

        Only ids that really exist are returned — a link is never fabricated.
        Sale/purchase links are read when those apps are installed (this module
        does not depend on them), and come from the invoice line's own link, the
        same one Odoo's invoice form follows.
        """
        ids = {'payment_id': line.move_id.origin_payment_id.id or False,
               'sale_order_id': False, 'purchase_order_id': False}
        if 'sale_line_ids' in line._fields:
            ids['sale_order_id'] = line.sale_line_ids[:1].order_id.id or False
        if 'purchase_line_id' in line._fields:
            ids['purchase_order_id'] = line.purchase_line_id.order_id.id or False
        return ids

    # ---- optional per-row detail (lazy, for the on-screen viewer) --------

    def _nc_has_row_detail(self):
        """True when ``_nc_row_detail`` can expand a summary row into the
        documents behind it (Aged: a partner's open invoices)."""
        return False

    def _nc_row_detail(self, row_key):
        """Rows behind one summary row, loaded only when the user opens it."""
        raise NotImplementedError
