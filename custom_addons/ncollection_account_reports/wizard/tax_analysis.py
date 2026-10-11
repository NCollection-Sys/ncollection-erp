# -*- coding: utf-8 -*-
"""Tax Analysis: output vs input tax, the way an accountant asks the question.

WHY THIS EXISTS. Odoo Community ships four ``account.report`` tax *definitions*
but not the renderer (that is Enterprise), and the FTA VAT 201 return answers
only "what goes in each box". What Community and OCA do not give a
non-technical user is the everyday set of questions below, as one printable,
drill-down report:

    * how much tax did we charge / pay, per tax            (``tax``)
    * which tax account does it sit in                      (``account``)
    * which customers carry how much output tax            (``customer``)
    * which vendors carry how much input tax               (``vendor``)
    * how much of the business is taxable / zero-rated / outside scope (``taxability``)

Odoo owns the numbers — this only AGGREGATES ``account.move.line`` and never
recomputes a tax:

    tax amount  = journal items that ARE a tax line  (``tax_line_id`` set)
    base amount = product items that CARRY the tax   (``tax_ids`` set, not a tax line)

Both are signed the way the business reads them: a sales credit note REDUCES
output tax, a vendor refund REDUCES input tax (output = credit - debit,
input = debit - credit). Going through ``tax_ids`` also reports a 0 % / exempt
tax (no tax line exists for it), which a tax-line-only report would drop.

Side (sales vs purchases) is the tax's own ``type_tax_use`` for the tax/account
views, and the document type for the partner/taxability views, so a customer
never shows purchase tax and vice versa.
"""
from odoo import fields, models

SALE_TYPES = ('out_invoice', 'out_refund', 'out_receipt')
PURCHASE_TYPES = ('in_invoice', 'in_refund', 'in_receipt')


class NcollectionTaxAnalysis(models.TransientModel):
    _name = 'ncollection.account.report.tax.analysis'
    _inherit = ['ncollection.account.report']
    _description = 'NCollection Tax Analysis'

    group_by = fields.Selection(
        [('tax', 'By tax'),
         ('account', 'By tax account'),
         ('customer', 'By customer'),
         ('vendor', 'By vendor'),
         ('taxability', 'Taxable vs non-taxable')],
        default='tax', required=True, string='View',
        help="How the tax is broken down. Every view reads the same journal "
             "items, so their totals agree.")

    def _nc_report_title(self):
        return {
            'account': self.env._("Tax Analysis — by tax account"),
            'customer': self.env._("Tax Analysis — by customer"),
            'vendor': self.env._("Tax Analysis — by vendor"),
            'taxability': self.env._("Tax Analysis — taxable vs non-taxable"),
        }.get(self.group_by, self.env._("Tax Analysis"))

    def _nc_report_action_ref(self):
        return 'ncollection_account_reports.action_report_tax_analysis'

    def _nc_columns(self):
        return [
            {'key': 'label', 'label': self.env._("Description"), 'type': 'char'},
            {'key': 'base', 'label': self.env._("Taxable amount"), 'type': 'monetary'},
            {'key': 'tax_amount', 'label': self.env._("Tax"), 'type': 'monetary'},
        ]

    def _nc_export_filters(self):
        out = super()._nc_export_filters()
        out.append((self._fields['group_by'].get_description(self.env, ['string'])['string'],
                    dict(self._fields['group_by']._description_selection(self.env))
                    .get(self.group_by, '')))
        return out

    # ------------------------------------------------------------------
    # aggregates
    # ------------------------------------------------------------------
    def _nc_tax_domain(self):
        """ONE scope for every view, so their totals agree: sales and purchase documents
        (invoices, credit notes, receipts). Manual journal entries that carry a tax are
        deliberately out of scope - they have no customer/vendor side to report on."""
        return self._nc_move_line_domain() + [
            ('move_id.move_type', 'in', SALE_TYPES + PURCHASE_TYPES)]

    @staticmethod
    def _signed(side, value):
        """Output tax reads credit - debit (= -balance); input tax debit - credit."""
        return -value if side == 'sale' else value

    def _nc_tax_bases(self, extra=None):
        """{(tax, side): signed base} — product items carrying each tax."""
        AML = self.env['account.move.line']
        domain = self._nc_tax_domain() + [
            ('tax_ids', '!=', False), ('tax_line_id', '=', False),
            ('display_type', '=', 'product')] + (extra or [])
        out = {}
        for tax, balance in AML._read_group(
                domain, groupby=['tax_ids'], aggregates=['balance:sum']):
            side = tax.type_tax_use
            if side not in ('sale', 'purchase'):
                continue
            out[(tax, side)] = out.get((tax, side), 0.0) + self._signed(side, balance)
        return out

    # ------------------------------------------------------------------
    # views
    # ------------------------------------------------------------------
    def _nc_section(self, label, rows, base=True):
        """A bold section header carrying the totals, then its rows."""
        header = {
            'label': label, 'account_id': False, 'level': 0,
            'base': sum(r.get('base') or 0.0 for r in rows) if base else None,
            'tax_amount': sum(r.get('tax_amount') or 0.0 for r in rows)}
        return [header] + rows

    def _nc_side_titles(self):
        return {'sale': self.env._("Output tax — sales"),
                'purchase': self.env._("Input tax — purchases")}

    def _nc_net_row(self, output, input_):
        return {'label': self.env._("Net tax payable (output − input)"),
                'account_id': False, 'level': 0, 'base': None,
                'tax_amount': output - input_}

    def _nc_compute_lines(self):
        self.ensure_one()
        return getattr(self, '_nc_view_%s' % self.group_by)()

    def _nc_view_tax(self):
        per_tax = {}
        for (tax, side), base in self._nc_tax_bases().items():
            per_tax.setdefault((tax, side), {'base': 0.0, 'tax_amount': 0.0})['base'] += base
        AML = self.env['account.move.line']
        for tax, balance in AML._read_group(
                self._nc_tax_domain() + [('tax_line_id', '!=', False)],
                groupby=['tax_line_id'], aggregates=['balance:sum']):
            side = tax.type_tax_use
            if side in ('sale', 'purchase'):
                per_tax.setdefault((tax, side), {'base': 0.0, 'tax_amount': 0.0})[
                    'tax_amount'] += self._signed(side, balance)
        titles = self._nc_side_titles()
        lines, totals = [], {}
        for side in ('sale', 'purchase'):
            rows = [
                {'label': tax.display_name, 'tax_id': tax.id, 'account_id': False, 'level': 1,
                 'base': vals['base'], 'tax_amount': vals['tax_amount']}
                for (tax, s), vals in sorted(per_tax.items(), key=lambda kv: kv[0][0].display_name)
                if s == side]
            if rows:
                # a line carrying two taxes shows its base under each, so a section base would double count
                lines += self._nc_section(titles[side], rows, base=False)
            totals[side] = sum(r['tax_amount'] for r in rows)
        if lines:
            lines.append(self._nc_net_row(totals['sale'], totals['purchase']))
        return lines

    def _nc_view_account(self):
        AML = self.env['account.move.line']
        titles = self._nc_side_titles()
        per = {}
        for account, tax, balance in AML._read_group(
                self._nc_tax_domain() + [('tax_line_id', '!=', False)],
                groupby=['account_id', 'tax_line_id'], aggregates=['balance:sum']):
            side = tax.type_tax_use
            if side in ('sale', 'purchase'):
                per[(account, side)] = per.get((account, side), 0.0) + self._signed(side, balance)
        lines, totals = [], {}
        for side in ('sale', 'purchase'):
            rows = [
                {'label': account.display_name, 'account_id': account.id, 'level': 1,
                 'base': None, 'tax_amount': amount}
                for (account, s), amount in sorted(per.items(), key=lambda kv: kv[0][0].code or '')
                if s == side]
            if rows:
                lines += self._nc_section(titles[side], rows, base=False)
            totals[side] = sum(r['tax_amount'] for r in rows)
        if lines:
            lines.append(self._nc_net_row(totals['sale'], totals['purchase']))
        return lines

    def _nc_partner_view(self, side, move_types):
        AML = self.env['account.move.line']
        scope = [('move_id.move_type', 'in', move_types)]
        base_domain = self._nc_tax_domain() + scope + [
            ('tax_ids', '!=', False), ('tax_line_id', '=', False),
            ('display_type', '=', 'product')]
        tax_domain = self._nc_tax_domain() + scope + [('tax_line_id', '!=', False)]
        per = {}
        for partner, balance in AML._read_group(
                base_domain, groupby=['partner_id'], aggregates=['balance:sum']):
            per.setdefault(partner, {'base': 0.0, 'tax_amount': 0.0})['base'] += self._signed(side, balance)
        for partner, balance in AML._read_group(
                tax_domain, groupby=['partner_id'], aggregates=['balance:sum']):
            per.setdefault(partner, {'base': 0.0, 'tax_amount': 0.0})['tax_amount'] += self._signed(side, balance)
        rows = [
            {'label': partner.display_name or self.env._("(no partner)"),
             'partner_id': partner.id or False, 'link_partner': bool(partner), 'account_id': False,
             'level': 1, 'base': vals['base'], 'tax_amount': vals['tax_amount']}
            for partner, vals in sorted(per.items(), key=lambda kv: (kv[0].display_name or ''))]
        if not rows:
            return []
        title = self._nc_side_titles()[side]
        return self._nc_section(title, rows)

    def _nc_view_customer(self):
        return self._nc_partner_view('sale', SALE_TYPES)

    def _nc_view_vendor(self):
        return self._nc_partner_view('purchase', PURCHASE_TYPES)

    def _nc_view_taxability(self):
        AML = self.env['account.move.line']
        categories = (
            ('taxable', self.env._("Taxable (standard rated)"),
             [('tax_ids', 'any', [('amount', '!=', 0)])]),
            ('zero', self.env._("Zero-rated / exempt (tax at 0 %)"),
             [('tax_ids', '!=', False), ('tax_ids', 'not any', [('amount', '!=', 0)])]),
            ('none', self.env._("Outside tax scope (no tax)"),
             [('tax_ids', '=', False)]),
        )
        lines = []
        for side, move_types in (('sale', SALE_TYPES), ('purchase', PURCHASE_TYPES)):
            scope = self._nc_tax_domain() + [
                ('move_id.move_type', 'in', move_types), ('tax_line_id', '=', False),
                ('display_type', '=', 'product')]
            tax_total = sum(
                self._signed(side, b) for _p, b in AML._read_group(
                    self._nc_tax_domain() + [('move_id.move_type', 'in', move_types),
                                             ('tax_line_id', '!=', False)],
                    groupby=['partner_id'], aggregates=['balance:sum']))
            rows = []
            for key, label, extra in categories:
                base = sum(self._signed(side, b) for _c, b in AML._read_group(
                    scope + extra, groupby=['company_id'], aggregates=['balance:sum']))
                rows.append({'label': label, 'account_id': False, 'level': 1, 'base': base,
                             'tax_amount': tax_total if key == 'taxable' else 0.0})
            if any(r['base'] or r['tax_amount'] for r in rows):
                lines += self._nc_section(self._nc_side_titles()[side], rows)
        return lines
