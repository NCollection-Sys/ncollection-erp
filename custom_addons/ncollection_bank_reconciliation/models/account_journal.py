"""The bank / cash journal card: Transactions · To Reconcile · Statements · New · Upload.

Each entry point is a journal method so the journal is always ``self`` (never an id taken from the client context),
and each one returns an action scoped to that journal. Transactions includes reconciled lines; To Reconcile is the
same workspace filtered to what still needs matching; Statements stays the separate native statement list.
"""
from odoo import models
from odoo.exceptions import UserError


class AccountJournal(models.Model):
    _inherit = 'account.journal'

    def _nc_bank_journal(self):
        self.ensure_one()
        if self.type not in ('bank', 'cash'):
            raise UserError(self.env._("%(journal)s is not a bank or cash journal.", journal=self.display_name))
        self.check_access('read')
        return self

    def _nc_scoped(self, xmlid, extra_context=None):
        journal = self._nc_bank_journal()
        action = self.env['ir.actions.act_window']._for_xml_id(xmlid)
        action['domain'] = [('journal_id', '=', journal.id)]
        context = {
            'default_journal_id': journal.id,
            'active_id': journal.id,
            'active_model': 'account.journal',
            'search_default_journal_id': journal.id,
            'view_ref': 'account_reconcile_oca.bank_statement_line_form_reconcile_view',
        }
        context.update(extra_context or {})
        action['context'] = context
        action['display_name'] = '%s · %s' % (journal.display_name, action.get('name') or '')
        return action

    def action_nc_bank_transactions(self):
        """Every transaction of the journal, reconciled ones included."""
        return self._nc_scoped('account_reconcile_oca.action_bank_statement_line_reconcile_all')

    def action_nc_bank_to_reconcile(self):
        """Only what still needs matching (the "Not reconciled" filter, which the user can remove)."""
        return self._nc_scoped('account_reconcile_oca.action_bank_statement_line_reconcile',
                               {'search_default_not_reconciled': True})

    def action_nc_bank_statements(self):
        journal = self._nc_bank_journal()
        xmlid = ('account.action_bank_statement_tree' if journal.type == 'bank'
                 else 'account.action_view_bank_statement_tree')
        action = self.env['ir.actions.act_window']._for_xml_id(xmlid)
        action['domain'] = [('journal_id', '=', journal.id)]
        action['context'] = {'default_journal_id': journal.id, 'search_default_journal_id': journal.id}
        return action

    def action_nc_bank_new(self):
        """A manual transaction (date, label, partner, amount) in this journal."""
        journal = self._nc_bank_journal()
        journal.env['account.bank.statement.line'].check_access('create')
        action = self.env['ir.actions.act_window']._for_xml_id(
            'account_reconcile_oca.action_bank_statement_line_create')
        action['context'] = {'default_journal_id': journal.id}
        return action

    def _get_bank_statements_available_import_formats(self):
        return super()._get_bank_statements_available_import_formats() + ['OFX']

    def action_nc_bank_duplicates(self):
        """The duplicate review of this journal (read-only detection; nothing is deleted from it)."""
        journal = self._nc_bank_journal()
        review = self.env['ncollection.bank.duplicate.review'].create({'journal_id': journal.id})
        return review.action_refresh()

    def action_nc_bank_batches(self):
        journal = self._nc_bank_journal()
        action = self.env['ir.actions.act_window']._for_xml_id('ncollection_bank_reconciliation.action_payment_batch')
        action['domain'] = [('journal_id', '=', journal.id)]
        action['context'] = {'default_journal_id': journal.id}
        return action

    def import_account_statement(self):
        """OCA's own "Import" entry point on the journal previews first too."""
        action = super().import_account_statement()
        action['context'] = dict(action.get('context') or {}, nc_require_preview=True)
        return action

    def action_nc_bank_upload(self):
        """The statement import wizard, bound to this journal: a file for another account is refused."""
        journal = self._nc_bank_journal()
        if journal.type != 'bank':
            raise UserError(self.env._("Statement files can only be imported into a bank journal."))
        journal.env['account.bank.statement.line'].check_access('create')
        action = journal.import_account_statement()
        action['context'] = dict(action.get('context') or {}, journal_id=journal.id, nc_strict_journal=True,
                                 nc_require_preview=True)
        return action
