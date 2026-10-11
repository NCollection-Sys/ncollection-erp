# -*- coding: utf-8 -*-
"""Print forms: how each report arranges the shared print design system.

Loaded after every report model exists (see ``wizard/__init__``), because it extends them. A report that
needs more than the default single table says so here: the ledgers print one titled section per account /
partner with opening, movements, a total and the closing balance; the statement prints one document per
partner with a summary band; Aged states the total outstanding. Everything is formatted from the rows the
engine already computed - no figure is calculated by a template.
"""
from odoo import models
from odoo.tools import format_date


class GeneralLedgerPrint(models.TransientModel):
    _inherit = 'ncollection.account.report.general.ledger'

    def _nc_print_sections(self, payload, lines):
        accounts = {a.id: a for a in self.env['account.account'].browse(
            {ln['account_id'] for ln in lines if ln.get('account_id')})}
        return self._nc_group_sections(
            payload, lines, 'account_id',
            lambda ident: accounts[ident].display_name if ident in accounts else '',
            self.env._("Total"))

    def _nc_print_summary(self, payload, lines, sections):
        _ = self.env._
        return [
            (_("Journal items"), str(sum(1 for ln in lines if not ln.get('is_initial'))), ''),
            (_("Total debit"), self._nc_amount(sum(s['debit'] for s in sections)), ''),
            (_("Total credit"), self._nc_amount(sum(s['credit'] for s in sections)), ''),
        ]


class PartnerLedgerPrint(models.TransientModel):
    _inherit = 'ncollection.account.report.partner.ledger'

    def _nc_print_sections(self, payload, lines):
        partners = {p.id: p for p in self.env['res.partner'].browse(
            {ln['partner_id'] for ln in lines if ln.get('partner_id')})}
        return self._nc_group_sections(
            payload, lines, 'partner_id',
            lambda ident: partners[ident].display_name if ident in partners else '',
            self.env._("Total"))

    def _nc_print_summary(self, payload, lines, sections):
        _ = self.env._
        return [
            (_("Opening balance"), self._nc_amount(sum(s['opening'] for s in sections)), ''),
            (_("Total debit"), self._nc_amount(sum(s['debit'] for s in sections)), ''),
            (_("Total credit"), self._nc_amount(sum(s['credit'] for s in sections)), ''),
            (_("Closing balance"), self._nc_amount(sum(s['closing'] for s in sections)), 'strong'),
        ]


class AgedPartnerPrint(models.TransientModel):
    _inherit = 'ncollection.account.report.aged.partner'

    def _nc_print_summary(self, payload, lines, sections):
        rows = [ln for ln in lines if ln.get('level', 1)]
        if not rows:
            return []
        return [(self.env._("Total outstanding"), self._nc_amount(sum(r.get('total') or 0.0 for r in rows)), 'strong'),
                (self.env._("Partners"), str(len(rows)), '')]


class StatementPrint(models.TransientModel):
    _inherit = 'ncollection.account.report.statement'

    def _nc_statement_columns(self):
        _ = self.env._
        return [
            {'key': 'date', 'label': _("Date"), 'kind': 'date', 'numeric': False},
            {'key': 'move_name', 'label': _("Reference"), 'kind': 'text', 'numeric': False},
            {'key': 'label', 'label': _("Description"), 'kind': 'text', 'numeric': False},
            {'key': 'debit', 'label': _("Debit"), 'kind': 'amount', 'numeric': True},
            {'key': 'credit', 'label': _("Credit"), 'kind': 'amount', 'numeric': True},
            {'key': 'running_balance', 'label': _("Balance"), 'kind': 'amount', 'numeric': True},
        ]

    def _nc_statement_party_label(self):
        _ = self.env._
        return {'receivable': _("Customer"), 'payable': _("Vendor")}.get(self.partner_scope, _("Partner"))

    def _nc_statement_party(self, partner):
        address = partner._display_address(without_company=True) or ''
        vat = (partner.country_id or self.company_id.country_id).vat_label or self.env._("Tax ID")
        return {
            'name': partner.display_name or '',
            'code': partner.ref or '',
            'lines': [x.strip() for x in address.split('\n') if x.strip()],
            'vat': {'label': vat, 'value': partner.vat} if partner.vat else False,
        }

    def _nc_print_payload(self):
        """One print document per partner, each with its own summary band and transaction table."""
        self.ensure_one()
        _ = self.env._
        env = self.env
        payload = dict(self._nc_header_payload())
        payload['generated'] = self._nc_ltr(payload['generated'])
        # the party is already the page's heading, so the partner filter is not repeated in the meta block
        party_filter = self._fields['partner_ids'].get_description(env, ['string'])['string']
        columns = self._nc_statement_columns()
        docs = []
        for st in self._nc_statement_docs():
            lines = st['lines']
            debit = sum(ln['debit'] for ln in lines)
            credit = sum(ln['credit'] for ln in lines)
            rows = [{'cls': 'opening', 'cells': [
                self._nc_cell(''), self._nc_cell(''), self._nc_cell(_("Opening balance")),
                self._nc_cell('', True), self._nc_cell('', True),
                self._nc_cell(self._nc_amount(st['opening']), True, st['opening'])]}]
            for ln in lines:
                rows.append({'cls': '', 'cells': [
                    dict(self._nc_cell(format_date(env, ln['date']) if ln.get('date') else ''), cls='dt'),
                    self._nc_cell(ln.get('move_name') or ''),
                    self._nc_cell(ln.get('label') or ''),
                    self._nc_cell(self._nc_amount(ln['debit']), True, ln['debit'], blank_zero=True),
                    self._nc_cell(self._nc_amount(ln['credit']), True, ln['credit'], blank_zero=True),
                    self._nc_cell(self._nc_amount(ln['running_balance']), True, ln['running_balance'])]})
            rows.append({'cls': 'grand', 'cells': [
                self._nc_cell(''), self._nc_cell(''), self._nc_cell(_("Closing balance")),
                self._nc_cell(self._nc_amount(debit), True, debit),
                self._nc_cell(self._nc_amount(credit), True, credit),
                self._nc_cell(self._nc_amount(st['closing']), True, st['closing'])]})
            docs.append({
                'party': self._nc_statement_party(st['partner']),
                'summary': [
                    (_("Opening balance"), self._nc_amount(st['opening']), ''),
                    (_("Total debit"), self._nc_amount(debit), ''),
                    (_("Total credit"), self._nc_amount(credit), ''),
                    (_("Closing balance"), self._nc_amount(st['closing']), 'strong')],
                'sections': [{'title': '', 'subtitle': '', 'rows': rows}],
            })
        payload.update(
            rtl=payload['direction'] == 'rtl', brand=self._nc_print_brand(), co=self._nc_print_company(),
            columns=columns, widths=self._nc_print_widths(columns), docs=docs,
            party_label=self._nc_statement_party_label(), code_label=_("Code"),
            period=self._nc_period(payload),
            meta=([(_("Period"), self._nc_period(payload))] if self._nc_period(payload) else [])
            + [(_("Currency"), payload['currency'])]
            + ([(_("Entries"), payload['entries'])] if payload['entries'] else [])
            + [f for f in payload['filters'] if f[0] != party_filter]
            + [(_("Generated"), payload['generated']), (_("Generated by"), payload['user'])])
        return self._nc_bidi_payload(payload) if payload['rtl'] else payload
