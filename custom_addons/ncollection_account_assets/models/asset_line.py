# -*- coding: utf-8 -*-
"""F4-T03: ONE answer to "is this depreciation line real money in the GL?".

This module asks that question in two places — the transfer wizard, deciding how
much accumulated depreciation a reclassification entry must move, and the Asset
Register, deciding what to report. They started as two hand-written
implementations that happened to agree.

`code-reviewer` flagged the shape rather than a defect, and was right to: this
repo's regression ledger is mostly two copies of one rule drifting while both
kept passing (#330, #348, #311). The account-type guard in this same module gets
a test that cross-checks it against `cash_flow.py`'s tuples; this predicate had
no equivalent, so it gets a single definition instead.

THE RULE, and why each clause is there:

* only ``depreciate`` lines. ``create`` is the opening base and ``remove`` is a
  disposal — neither is a depreciation charge.
* ``init_entry`` counts **unconditionally, with no move**. These are the
  accumulated depreciation of an asset migrated in from a previous system, for
  which Odoo generates no document by design. Keying on ``move_id`` would drop
  exactly the assets whose history matters most, reporting them at full gross
  with zero accumulated — an overstated net book value, silently.
* otherwise a posted move is required, and ``target_move`` decides whether draft
  counts, so a report ties back to whichever GL view the user asked for.

The transfer wizard deliberately passes ``'posted'`` and never ``'all'``: a
reclassification may only move value the general ledger has actually recognised.
Moving a draft amount would leave the two asset accounts permanently out of step
with their sub-ledger. That is a different question with a different answer, not
a drift — and it is now visible as an argument rather than hidden in a second
copy of the code.
"""
from odoo import models


class AccountAssetLine(models.Model):
    _inherit = 'account.asset.line'

    def _nc_is_in_gl(self, target_move='posted'):
        """True when this line represents real general-ledger money.

        ``target_move`` takes the F2 engine's values (``'posted'`` / ``'all'``)
        so a caller can hand its own filter straight through.
        """
        self.ensure_one()
        if self.type != 'depreciate':
            return False
        if self.init_entry:
            return True
        if not self.move_id:
            return False
        if target_move == 'posted':
            return self.move_id.state == 'posted'
        return self.move_id.state in ('posted', 'draft')
