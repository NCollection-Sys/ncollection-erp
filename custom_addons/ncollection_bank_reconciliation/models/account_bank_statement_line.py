"""Safety layer over the OCA reconciliation engine (account_reconcile_oca).

The engine does the matching and calls Odoo's own ``account.move.line.reconcile()``; this layer adds what it lacks:

* one reconciliation at a time per transaction: the row is locked ``FOR UPDATE NOWAIT`` and re-read, so a second
  user (or a double click, or a replayed RPC) gets a clear refusal instead of a second set of counterpart lines;
* a transaction that is already reconciled cannot be reconciled again, and a counterpart journal item that is already
  fully reconciled cannot be used (it would be paid twice);
* every counterpart must belong to the transaction's company;
* undo only applies to a reconciled transaction and leaves an audit note on the journal entry, naming the user and
  the counterparts released; posted entries are never deleted (OCA ``keep`` mode reverses them, ``edit`` mode is
  Odoo's own ``action_undo_reconciliation``);
* write access is checked with the ORM (``check_access``), never bypassed with sudo: a read-only (CEO) or billing
  user is refused at the same layer the menu hides the buttons.

It also fixes OCA/account-reconcile#1036 (KeyError ``original_amount_unsigned`` in "full paid" when the line is
already allocated in full). OCA/account-reconcile#995 (a false exchange difference) is covered by a test instead
of an override: the exchange line it reports is a real gain/loss in company currency, and the test proves the
result balances and is booked once (see tests/test_reconciliation.py).
"""
from markupsafe import Markup, escape
from psycopg2 import errors as pg_errors

from odoo import models
from odoo.exceptions import UserError


class AccountBankStatementLine(models.Model):
    _inherit = 'account.bank.statement.line'

    # ------------------------------------------------------------------ guards
    def _nc_lock_rows(self, table, ids):
        """``FOR UPDATE NOWAIT`` inside a savepoint: a held lock is refused at once, and the refusal does not abort
        the caller's transaction. ``table`` is one of two literals below, never user input."""
        if not ids:
            return
        try:
            with self.env.cr.savepoint(flush=False):
                self.env.cr.execute(f'SELECT id FROM {table} WHERE id IN %s FOR UPDATE NOWAIT',  # noqa: S608
                                    [tuple(ids)])
        except pg_errors.LockNotAvailable:
            raise UserError(self.env._(
                "Another user is reconciling this bank transaction or one of its counterparts right now. Reload it "
                "and try again.")) from None

    def _nc_lock(self):
        """Lock the transaction rows; refuse at once if another transaction holds them."""
        self.env.flush_all()
        self._nc_lock_rows('account_bank_statement_line', self.ids)
        self.invalidate_recordset(['is_reconciled', 'amount_residual'])

    def _nc_counterpart_lines(self):
        """Journal items of OTHER entries the pending reconciliation would match: the explicit counterparts, and the
        items the engine proposed itself (they carry their own id, on another move)."""
        ids = set()
        for line in (self.reconcile_data_info or {}).get('data', []):
            ids.update(line.get('counterpart_line_ids') or [])
            if line.get('kind') == 'other' and line.get('id') and line.get('move_id') != self.move_id.id:
                ids.add(line['id'])
        return self.env['account.move.line'].browse(sorted(ids)).exists()

    def _nc_check_can_reconcile(self):
        self.check_access('write')
        self._nc_lock()
        for st_line in self:
            if st_line.is_reconciled:
                raise UserError(st_line.env._(
                    "The bank transaction %(name)s is already reconciled. Undo the reconciliation first if it is "
                    "wrong.", name=st_line.display_name))
            # The workspace only offers Validate when the engine says the entry can be booked; an RPC call must not
            # get past what the button hides (Rule 4).
            if not (st_line.reconcile_data_info or {}).get('can_reconcile'):
                raise UserError(st_line.env._(
                    "The bank transaction %(name)s still has an amount that is not allocated to anything. Match it, "
                    "set an account, or set the partner first.", name=st_line.display_name))
            counterparts = st_line._nc_counterpart_lines()
            if counterparts:
                st_line._nc_lock_rows('account_move_line', counterparts.ids)
                counterparts.invalidate_recordset(['reconciled', 'amount_residual', 'amount_residual_currency'])
            foreign = counterparts.filtered(lambda aml: aml.company_id != st_line.company_id)
            if foreign:
                raise UserError(st_line.env._(
                    "A bank transaction of %(company)s cannot be matched with journal items of another company.",
                    company=st_line.company_id.display_name))
            settled = counterparts.filtered('reconciled')
            if settled:
                raise UserError(st_line.env._(
                    "These journal items are already fully reconciled and cannot be matched again: %(items)s",
                    items=', '.join(settled.mapped('display_name'))))

    def _nc_audit(self, body):
        for st_line in self:
            st_line.move_id.message_post(body=body, message_type='notification')

    # --------------------------------------------------------------- overrides
    def reconcile_bank_line(self):
        self.ensure_one()
        self._nc_check_can_reconcile()
        counterparts = self._nc_counterpart_lines()
        result = super().reconcile_bank_line()
        names = ', '.join(counterparts.mapped('move_id.name')) or self.env._('an account (no document)')
        self._nc_audit(Markup('%s') % escape(self.env._(
            "Bank transaction matched by %(user)s with %(items)s.", user=self.env.user.name, items=names)))
        return result

    def unreconcile_bank_line(self):
        self.ensure_one()
        self.check_access('write')
        self._nc_lock()
        if not self.is_reconciled:
            raise UserError(self.env._("The bank transaction %(name)s is not reconciled; there is nothing to undo.",
                                       name=self.display_name))
        released = self.line_ids._all_reconciled_lines().filtered(lambda aml: aml.move_id != self.move_id)
        names = ', '.join(sorted(set(released.mapped('move_id.name')))) or self.env._('an account (no document)')
        result = super().unreconcile_bank_line()
        # The engine resets the pending reconciliation BEFORE it undoes the match, i.e. from the old journal items:
        # the next Validate would then re-book the released counterpart as an unmatched receivable / payable line
        # (balanced, but the invoice stays open and the customer shows a credit as well). Rebuild it from what is
        # there now.
        self.invalidate_recordset()
        self.reconcile_data_info = self._default_reconcile_data()
        self._nc_audit(Markup('%s') % escape(self.env._(
            "Reconciliation undone by %(user)s; released: %(items)s.", user=self.env.user.name, items=names)))
        return result

    def _recompute_suspense_line(self, data, reconcile_auxiliary_id, manual_reference):
        """A remainder already routed to the transaction partner's receivable / payable is bookable.

        The engine treats it that way when it builds the remainder itself (a transaction with a partner and nothing
        matched), but not once an invoice was matched first: an overpayment then shows its remainder on the
        customer's receivable with Validate disabled, and re-selecting that same account changes nothing, so the user
        is stuck. Same rule, applied consistently: only in ``edit`` mode (``keep`` refuses remainder lines), only for
        the partner's own receivable / payable, and only when every other line has a real account."""
        res = super()._recompute_suspense_line(data, reconcile_auxiliary_id, manual_reference)
        if res.get('can_reconcile') or not self.partner_id or (self.journal_id.reconcile_mode or 'edit') != 'edit':
            return res
        remainder = [d for d in res.get('data', []) if d.get('kind') == 'suspense']
        partner = self.partner_id
        partner_accounts = {partner.property_account_receivable_id.id, partner.property_account_payable_id.id}
        suspense_account = self.journal_id.suspense_account_id.id
        others = [d for d in res.get('data', []) if d.get('kind') != 'suspense']
        to_partner = len(remainder) == 1 and remainder[0].get('account_id') \
            and remainder[0]['account_id'][0] in partner_accounts
        if (to_partner
                and all(d.get('account_id') and d['account_id'][0] != suspense_account for d in others)):
            res['can_reconcile'] = True
        return res

    def button_manual_reference_full_paid(self):
        """OCA/account-reconcile#1036: the engine only writes ``original_amount_unsigned`` when the allocated amount
        differs from the open amount, then reads it unconditionally. A line already allocated in full has nothing
        left to pay, so "full paid" is a no-op for it instead of a KeyError."""
        self.ensure_one()
        info = self.reconcile_data_info or {}
        reference = info.get('manual_reference')
        for line in info.get('data', []):
            if line.get('reference') == reference and line.get('id') and 'original_amount_unsigned' not in line:
                return None
        return super().button_manual_reference_full_paid()
