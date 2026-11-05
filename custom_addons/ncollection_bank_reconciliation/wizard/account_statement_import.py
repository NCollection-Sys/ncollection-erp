"""Strict statement import on top of the OCA wizard (account_statement_import_file and its format modules).

What the OCA wizard does not do, and this adds:

* **Preview before import.** The file is parsed, matched to its journal and checked for duplicates without writing
  anything; Import is accepted only for the exact file that was previewed (SHA-256), so what the user saw is what
  is booked.
* **Exact journal.** OCA finds the journal with ``ilike`` on the account number, so a file for account ``1234`` can
  land in the journal of ``41234``; the match must be exact here. The journal must be a bank journal of the ACTIVE
  company, and an upload started from a journal card can only import into that journal.
* **Duplicates without a bank transaction id.** OCA de-duplicates only lines that carry ``unique_import_id``; a
  CSV / XLSX export without a transaction-id column would be imported twice. Such a line gets a deterministic
  fingerprint (journal, date, amount, label, reference, and the occurrence number among identical lines in the same
  file), so re-importing the same file, or an overlapping one, skips what is already there while two genuinely
  identical lines in one file both stay.
* **No silent row loss.** The OCA sheet parser drops a row whose currency differs from the journal's; that row is a
  per-row error here, so a file is imported entirely or not at all (one database transaction).
"""
import base64
import hashlib
from collections import Counter

from markupsafe import Markup, escape

from odoo import fields, models
from odoo.exceptions import UserError
from odoo.tools import formatLang

from odoo.addons.base.models.res_bank import sanitize_account_number


class AccountStatementImport(models.TransientModel):
    _inherit = 'account.statement.import'

    nc_preview_html = fields.Html(string='Preview', readonly=True, sanitize=True)
    nc_preview_checksum = fields.Char(string='Previewed file (SHA-256)', readonly=True)
    nc_preview_count = fields.Integer(string='New transactions in the preview', readonly=True)
    nc_skipped_count = fields.Integer(string='Rows left out (other currency)', readonly=True)
    nc_new_total = fields.Float(string='Total of the new transactions', readonly=True)
    nc_accept_partial = fields.Boolean(
        string='Import only the rows in the journal currency',
        help='The file has rows in another currency. By default nothing is imported; tick this to import the rows in '
             'the journal currency only. The left-out rows are recorded on the statement.')

    # ------------------------------------------------------------- parsing
    def _parse_file(self, data_file):
        """A format without a currency (QIF) takes the journal's currency from ``journal.currency_id`` only, which
        is empty on a journal in the company currency, so the import failed with "Missing currency code". The
        journal's EFFECTIVE currency is used instead (the journal currency, else the company's)."""
        result = super()._parse_file(data_file)
        journal = self.env['account.journal'].browse(self.env.context.get('journal_id')).exists()
        if not journal:
            return result
        effective = (journal.currency_id or journal.company_id.currency_id).name

        def fill(single):
            if isinstance(single, tuple) and len(single) == 3 and not single[0]:
                return (effective, single[1], single[2])
            return single
        return [fill(s) for s in result] if isinstance(result, list) else fill(result)

    # ------------------------------------------------------------- journal
    def _match_journal(self, account_number, currency):
        journal = super()._match_journal(account_number, currency)
        if journal.type != 'bank':
            raise UserError(self.env._("Statements can only be imported into a bank journal."))
        if journal.company_id != self.env.company:
            raise UserError(self.env._(
                "The journal %(journal)s belongs to %(company)s, not to the active company %(active)s. Switch "
                "company first.", journal=journal.display_name, company=journal.company_id.display_name,
                active=self.env.company.display_name))
        if account_number:
            wanted = sanitize_account_number(account_number)
            if (journal.bank_account_id.sanitized_acc_number or '') != wanted:
                raise UserError(self.env._(
                    "The file is for the bank account %(number)s, which is not exactly the account of %(journal)s.",
                    number=account_number, journal=journal.display_name))
        ctx_journal = self.env.context.get('journal_id')
        if self.env.context.get('nc_strict_journal') and ctx_journal and journal.id != ctx_journal:
            raise UserError(self.env._(
                "This file belongs to %(journal)s, not to the journal you are uploading into.",
                journal=journal.display_name))
        return journal

    # ---------------------------------------------------------- duplicates
    @staticmethod
    def _nc_fingerprint_key(journal, lvals):
        return '|'.join(str(part) for part in (
            journal.id, lvals.get('date') or '', journal.currency_id.round(float(lvals.get('amount') or 0.0))
            if journal.currency_id else round(float(lvals.get('amount') or 0.0), 2),
            (lvals.get('payment_ref') or '').strip(), (lvals.get('ref') or '').strip(),
            (lvals.get('account_number') or '').strip()))

    def _complete_stmts_vals(self, stmts_vals, journal, account_number):
        stmts_vals = super()._complete_stmts_vals(stmts_vals, journal, account_number)
        seen = Counter()
        for st_vals in stmts_vals:
            for lvals in st_vals['transactions']:
                if lvals.get('unique_import_id'):
                    continue
                key = self._nc_fingerprint_key(journal, lvals)
                seen[key] += 1
                digest = hashlib.sha256(f'{key}|{seen[key]}'.encode()).hexdigest()[:40]
                lvals['unique_import_id'] = f'nc-fp-{journal.id}-{digest}'
        return stmts_vals

    # ------------------------------------------------------------- preview
    def _nc_checksum(self):
        return hashlib.sha256(base64.b64decode(self.statement_file or b'')).hexdigest()

    def _nc_preview_rows(self):
        """Parse and match exactly as the import does, without creating anything."""
        self.ensure_one()
        skipped = []
        wizard = self.with_context(active_id=self.id, nc_skipped_rows=skipped)
        parsed = wizard._parse_file(base64.b64decode(self.statement_file))
        if not isinstance(parsed, list):
            parsed = [parsed]
        rows = []
        line_model = self.env['account.bank.statement.line']
        for single in parsed:
            if not isinstance(single, tuple):
                raise UserError(self.env._("The parsing of the statement file returned an invalid result."))
            currency_code, account_number, stmts_vals = single
            if not wizard._check_parsed_data(stmts_vals):
                continue
            if not currency_code:
                raise UserError(self.env._("Missing currency code in the bank statement file."))
            currency = wizard._match_currency(currency_code)
            journal = wizard._match_journal(account_number, currency)
            stmts_vals = wizard._complete_stmts_vals(stmts_vals, journal, account_number)
            for st_vals in stmts_vals:
                for lvals in st_vals['transactions']:
                    exists = bool(line_model.search_count([('unique_import_id', '=', lvals['unique_import_id'])]))
                    rows.append({'journal': journal, 'currency': currency, 'vals': lvals, 'duplicate': exists})
        if not rows:
            raise UserError(self.env._("The file contains no transactions."))
        return rows, skipped

    def action_nc_preview(self):
        self.ensure_one()
        rows, skipped = self._nc_preview_rows()
        new = [r for r in rows if not r['duplicate']]
        currency = rows[0]['currency']
        new_total = currency.round(sum(float(r['vals'].get('amount') or 0.0) for r in new))
        head = Markup('<p><b>%s</b></p>') % self.env._(
            "%(total)s transactions read for %(journal)s (%(currency)s): %(new)s new, %(dup)s already imported and "
            "skipped.", total=len(rows), journal=rows[0]['journal'].display_name, currency=currency.name,
            new=len(new), dup=len(rows) - len(new))
        head += Markup('<p class="o_nc_import_total">%s</p>') % self.env._(
            "Total of the new transactions: %(amount)s", amount=formatLang(self.env, new_total, currency_obj=currency))
        header = Markup('<tr><th>%s</th><th>%s</th><th>%s</th><th>%s</th><th class="text-end">%s</th>'
                        '<th>%s</th></tr>')
        cells = [header % (
            self.env._("Date"), self.env._("Label"), self.env._("Reference"), self.env._("Partner"),
            self.env._("Amount"), self.env._("Status"))]
        for row in rows:
            vals = row['vals']
            partner = vals.get('partner_name') or (
                self.env['res.partner'].browse(vals['partner_id']).display_name if vals.get('partner_id') else '')
            cells.append(Markup('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td class="text-end">%s</td>'
                                '<td>%s</td></tr>') % (
                escape(str(vals.get('date') or '')), escape(vals.get('payment_ref') or ''),
                escape(vals.get('ref') or ''),
                escape(partner), formatLang(self.env, float(vals.get('amount') or 0.0), currency_obj=row['currency']),
                self.env._("Already imported (skipped)") if row['duplicate'] else self.env._("New")))
        table = Markup('<table class="table table-sm o_nc_import_preview">%s</table>') % Markup('').join(cells)
        if skipped:
            items = Markup('').join(Markup('<li>%s %s · %s</li>') % (s['currency'], s['amount'], s['label'])
                                    for s in skipped)
            table += Markup('<div class="alert alert-warning o_nc_import_skipped"><b>%s</b><ul>%s</ul></div>') % (
                self.env._("%(count)s rows are in another currency. Nothing is imported unless you choose to import "
                           "only the rows in the journal currency; these would then be left out:", count=len(skipped)),
                items)
        self.write({'nc_preview_html': head + table, 'nc_preview_checksum': self._nc_checksum(),
                    'nc_preview_count': len(new), 'nc_skipped_count': len(skipped), 'nc_new_total': new_total,
                    'nc_accept_partial': False})
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id, 'view_mode': 'form',
                'target': 'new', 'context': dict(self.env.context), 'name': self.env._("Preview bank statement")}

    def import_file_button(self):
        self.ensure_one()
        # Every user-facing entry point (the journal card's Upload and the Accounting menu action) sets
        # nc_require_preview; a programmatic import (an OCA test, a script) creates the wizard without it.
        if self.env.context.get('nc_require_preview') or self.nc_preview_checksum:
            if self.nc_preview_checksum != self._nc_checksum():
                raise UserError(self.env._("Preview this file before importing it (the file changed or was not "
                                           "previewed)."))
            if not self.nc_preview_count:
                raise UserError(self.env._("Every transaction in this file is already imported; nothing to do."))
            if self.nc_skipped_count and not self.nc_accept_partial:
                raise UserError(self.env._(
                    "%(count)s rows of this file are in another currency, so the file would only be imported in part. "
                    "Nothing was imported. Tick \"Import only the rows in the journal currency\" to import the rest.",
                    count=self.nc_skipped_count))
        skipped, accepted = self.nc_skipped_count, self.nc_preview_count
        result = super().import_file_button()
        if self.nc_preview_checksum and skipped:
            # the statement has no chatter in Odoo 19: the record goes on its first transaction's journal entry
            action = result.get('params', {}).get('next', result) if result.get(
                'type') == 'ir.actions.client' else result
            ids = next((leaf[2] for leaf in action.get('domain') or [] if leaf[0] == 'id'), [])
            statements = self.env['account.bank.statement'].browse(ids).exists()
            for statement in statements:
                statement.line_ids.sorted('id')[:1].move_id.message_post(body=self.env._(
                    "Partial import accepted by %(user)s: %(accepted)s transactions imported, %(skipped)s rows in "
                    "another currency left out of %(file)s.", user=self.env.user.name, accepted=accepted,
                    skipped=skipped, file=self.statement_filename), message_type='notification')
        return result
