# -*- coding: utf-8 -*-
"""Capture of invoice amount changes (see ``invoice_audit.py`` and docs/markdown/INVOICE_AMOUNT_AUDIT.md).

HOW. ``amount_untaxed`` / ``amount_tax`` / ``amount_total`` are stored COMPUTED fields: editing a line moves them through
recompute-flush and never through ``account.move.write``, which is why a field-level rule on the move alone logs
nothing. So the three amounts are compared before and after the OUTERMOST ORM operation on a move or a move line:

* an operation nested inside another audited operation (a move write that writes its lines, a create with line commands)
  is covered by the outer one, so one user action is ONE entry, never several overlapping ones;
* an operation that leaves the amounts unchanged (a label, a note, a no-op write, posting) records nothing;
* an operation that fails raises before anything is recorded, and the transaction or savepoint is rolled back with it,
  so a rejected change (a posted invoice, a missing right) leaves no entry and no phantom amounts;
* nothing here weakens an accounting protection: the audited operation runs exactly as without this module, the audit
  only READS amounts (as ``sudo``, so a restricted user's change is recorded identically) and WRITES its own table.

NOT covered (stated, not hidden): changes made by raw SQL, and a recompute that no write on a move or line caused (for
example a change of a tax rate or a currency rate that re-prices a draft invoice later). Posted invoices are immutable
in Odoo, so their amounts change only after a reset to draft, which this module sees as an ordinary change.
"""
import threading
from contextlib import contextmanager

from odoo import api, models

_STATE = threading.local()


@contextmanager
def _outermost(cr):
    """Yield True for the outermost audited operation on this cursor and False for any operation nested in it."""
    active = getattr(_STATE, 'cursors', None)
    if active is None:
        active = _STATE.cursors = set()
    key = id(cr)
    if key in active:
        yield False
        return
    active.add(key)
    try:
        yield True
    finally:
        active.discard(key)


class AccountMove(models.Model):
    _inherit = 'account.move'

    # ------------------------------------------------------------------ snapshot / compare
    def _nc_audit_snapshot(self):
        """{move id: snapshot} for the invoices (and receipts) among ``self``; other entries are not audited."""
        snaps = {}
        for move in self.sudo().exists():
            if not move.is_invoice(include_receipts=True):
                continue
            snaps[move.id] = {
                'id': move.id,
                'name': move.name or move.display_name or '',
                'move_type': move.move_type,
                'state': move.state,
                'partner': move.partner_id.name or '',
                'company_id': move.company_id.id,
                'currency_id': move.currency_id.id,
                'amount_untaxed': move.amount_untaxed,
                'amount_tax': move.amount_tax,
                'amount_total': move.amount_total,
                # What the invoice LINES say. Core lets a line's price be edited on a posted invoice without re-pricing the
                # booked entry, so the booked total can stay put while the lines no longer agree with it; both are kept.
                'lines_total': sum(move.invoice_line_ids.filtered(
                    lambda line: line.display_type == 'product').mapped('price_total')),
            }
        return snaps

    @staticmethod
    def _nc_audit_same(left, right):
        return (left['currency_id'] == right['currency_id']
                and all(left[key] == right[key] for key in (
                    'amount_untaxed', 'amount_tax', 'amount_total', 'lines_total')))

    def _nc_audit_record_changes(self, before, trigger):
        """Compare ``before`` with the amounts now and record each net change as one entry."""
        after = self._nc_audit_snapshot()
        audit = self.env['ncollection.invoice.audit']
        for move_id, new in after.items():
            old = before.get(move_id)
            if old is not None and not self._nc_audit_same(old, new):
                audit._nc_record('amount_changed', old, new, trigger)

    # ------------------------------------------------------------------ hooks on account.move
    @api.model_create_multi
    def create(self, vals_list):
        with _outermost(self.env.cr) as top:
            moves = super().create(vals_list)
            if top:
                audit = self.env['ncollection.invoice.audit']
                for snap in moves._nc_audit_snapshot().values():
                    audit._nc_record('created', None, snap, 'account.move')
        return moves

    def write(self, vals):
        with _outermost(self.env.cr) as top:
            if not top:
                return super().write(vals)
            before = self._nc_audit_snapshot()
            result = super().write(vals)
            if before:
                self.browse(list(before))._nc_audit_record_changes(before, 'account.move')
        return result

    def unlink(self):
        with _outermost(self.env.cr) as top:
            if not top:
                return super().unlink()
            before = self._nc_audit_snapshot()
            result = super().unlink()
            audit = self.env['ncollection.invoice.audit']
            for snap in before.values():
                audit._nc_record('deleted', snap, None, 'account.move')
        return result


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    @api.model_create_multi
    def create(self, vals_list):
        with _outermost(self.env.cr) as top:
            if not top:
                return super().create(vals_list)
            moves = self.env['account.move'].browse(
                [vals['move_id'] for vals in vals_list if vals.get('move_id')]).exists()
            before = moves._nc_audit_snapshot()
            lines = super().create(vals_list)
            if before:
                moves._nc_audit_record_changes(before, 'account.move.line')
        return lines

    def write(self, vals):
        with _outermost(self.env.cr) as top:
            if not top:
                return super().write(vals)
            moves = self.move_id
            before = moves._nc_audit_snapshot()
            result = super().write(vals)
            if before:
                moves._nc_audit_record_changes(before, 'account.move.line')
        return result

    def unlink(self):
        with _outermost(self.env.cr) as top:
            if not top:
                return super().unlink()
            moves = self.move_id
            before = moves._nc_audit_snapshot()
            result = super().unlink()
            if before:
                moves._nc_audit_record_changes(before, 'account.move.line')
        return result
