# -*- coding: utf-8 -*-
"""F2-T04: the "whole entity" guard shared by the two statements that reconcile.

A General Ledger filtered to one journal is a smaller, still-correct General
Ledger. A **Cash Flow Statement** filtered to one journal is not a smaller cash
flow statement — it is a wrong one, because both statements in F2-T04 derive
their headline figure from an accounting identity that only holds over the
complete set of journal items:

* Cash Flow — ``operating + investing + financing == the movement on cash``
  holds only because every non-cash account is on the report. Drop a journal and
  the two sides stop being the same books.
* Changes in Equity — ties to the Balance Sheet's equity section, which is
  itself computed over everything.

The engine's ``journal_ids`` / ``account_ids`` / ``partner_ids`` filters are
inherited by every report, so these two would silently accept them and render a
statement that does not reconcile, with nothing on the face of it saying so.
Refusing is the only honest option: a financial statement that is quietly wrong
is worse than one that will not print.

Date, company and posted/draft are NOT restricted — they select *which* complete
set of books to report on, which is exactly what a period statement is for.
"""
from odoo import models
from odoo.exceptions import UserError

# The engine filters that carve a SUBSET out of the books, with the label the
# form shows for each. Company/date/target_move are deliberately absent.
_SUBSETTING_FILTERS = (
    ('journal_ids', "Journals"),
    ('account_ids', "Accounts"),
    ('partner_ids', "Partners"),
)


class NcollectionAccountReportWholeEntity(models.AbstractModel):
    _name = 'ncollection.account.report.whole.entity'
    _description = 'NCollection Report Requiring the Complete Books'

    def _nc_assert_whole_entity(self):
        """Refuse to render when a subsetting filter is set.

        Called from ``_nc_compute_lines`` rather than from a constraint, so it
        covers EVERY channel the engine renders through — on-screen list, PDF
        and XLSX all go through that one method — instead of only the paths that
        happen to write the field.
        """
        self.ensure_one()
        applied = [label for field, label in _SUBSETTING_FILTERS if self[field]]
        if applied:
            raise UserError(self.env._(
                "%(title)s is computed over the complete books and cannot be "
                "filtered by %(filters)s — the resulting figures would not "
                "reconcile. Remove the filter, or use the General Ledger, which "
                "is meaningful over any subset.",
                title=self._nc_report_title(), filters=", ".join(applied)))
