# -*- coding: utf-8 -*-
"""One "Duplicate" action for every business document that offers it (invoice, bill, receipt, quotation, order...).

It adds NOTHING of its own to the copy: the new record is built by the model's own ``copy()``, which is where Odoo
already decides what a duplicate is (a new number from the sequence, the draft state, no payments, no journal
entry, no stock moves, no chatter history, lock dates respected). Reimplementing any of that here would be a second,
drifting definition of "a copy", so this mixin only wraps it:

* it runs AS THE CALLING USER, so a user who may read a document but not create one gets Odoo's own AccessError
  rather than a record made behind their back (no ``sudo`` anywhere);
* it never writes to the original;
* it returns the form of the NEW record.

A model opts in by inheriting ``nc.duplicate.mixin`` and, when some documents must not be offered a copy, overriding
:meth:`_nc_duplicate_blocker`.
"""
from odoo import models
from odoo.exceptions import UserError


class NcDuplicateMixin(models.AbstractModel):
    _name = 'nc.duplicate.mixin'
    _description = 'Duplicate action for business documents'

    def _nc_duplicate_blocker(self):
        """A message when THIS document must not be duplicated, else ``False``. Override per model."""
        return False

    def action_nc_duplicate(self):
        self.ensure_one()
        self.check_access('read')
        self.check_access('create')                    # the same right "New" needs: Duplicate never grants more
        blocker = self._nc_duplicate_blocker()
        if blocker:
            raise UserError(blocker)
        new = self.copy()
        return {
            'type': 'ir.actions.act_window',
            'res_model': new._name,
            'res_id': new.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'current',
        }
