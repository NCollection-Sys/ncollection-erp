"""Rows the CSV / XLSX parser leaves out are reported, never lost silently.

``account_statement_import_sheet_file`` deliberately skips a row whose currency column differs from the journal's
(one multi-currency bank export is imported into one journal per currency; OCA's own test relies on it). Upstream
nobody is told. Here each skipped row is recorded in the list the preview passes in the context
(``nc_skipped_rows``), and the preview names them before anything is imported.
"""
from odoo import models


class AccountStatementImportSheetParser(models.TransientModel):
    _inherit = 'account.statement.import.sheet.parser'

    def _parse_one_line(self, mapping, currency_code, values, columns):
        line = super()._parse_one_line(mapping, currency_code, values, columns)
        skipped = self.env.context.get('nc_skipped_rows')
        if line is None and isinstance(skipped, list):
            currency = self._get_values_from_column(values, columns, 'currency_column')
            if isinstance(currency, str):
                currency = currency.strip()
            if currency and currency != currency_code:
                skipped.append({
                    'currency': currency,
                    'label': str(self._get_values_from_column(values, columns, 'description_column') or ''),
                    'amount': str(self._get_values_from_column(values, columns, 'amount_column') or ''),
                })
        return line
