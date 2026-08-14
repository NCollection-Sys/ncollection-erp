# -*- coding: utf-8 -*-
"""F4-T03: narrow OCA's profile-change guard so a TRANSFER can happen.

``account_asset_management`` refuses outright — ``models/account_asset.py``::

    @api.constrains("profile_id")
    def _check_profile_change(self):
        if self.depreciation_line_ids.filtered("move_id"):
            raise UserError(...)     # "You cannot change the profile of an
                                     #  asset with accounting entries."

**That guard is right, and it is not being removed.** Its reason is real: a bare
profile edit silently re-points FUTURE depreciation at new accounts while every
PAST entry stays against the old ones. Nothing moves the already-posted gross
cost and accumulated depreciation, so the asset sub-ledger and the general
ledger diverge permanently, and no report says so.

A transfer removes exactly that reason. ``ncollection.account.asset.transfer``
posts the reclassification entry FIRST — gross and accumulated both moved, four
legs, balance-sheet only — and only then writes the new profile. The GL ends up
describing the same asset in the new accounts, which is the state OCA's guard
exists to protect.

So the guard is correct for a bare edit and wrong for a transfer, and the fix is
to narrow it, not to delete it (Standing Rule 2: extend, never replace).

WHY THE EXEMPTION IS NOT A CONTEXT KEY — the interesting part
=============================================================

The first implementation of this file keyed the exemption on
``self.env.context['nc_asset_transfer_ids']``, reasoning that naming exact
record ids was safer than a bare boolean flag. **Narrower, but not safer: both
are equally forgeable.** ``odoo/service/model.py``::

    def call_kw(model, name, args, kwargs):
        ...
        context = kwargs.pop('context', None) or {}
        recs = recs.with_context(context)

There is no allowlist. Every RPC caller supplies the entire context verbatim, so
this was enough to disable a data-integrity constraint::

    execute_kw(db, uid, pwd, 'account.asset', 'write',
               [[victim_id], {'profile_id': other_profile_id}],
               {'context': {'nc_asset_transfer_ids': [victim_id]}})

— no reclassification entry, no wizard, no manager rights. And it is reachable
well below the wizard's gate: the transfer action and its ``ir.model.access``
row are both ``account.group_account_manager``, but OCA's own
``ir.model.access.csv`` grants ``perm_write`` on ``account.asset`` to
``account.group_account_invoice`` and ``account.group_account_user`` as well.
Caught in review, with the ``call_kw`` source cited; ``test_transfer.py`` now
performs that exact RPC-shaped call and asserts it is refused.

**Context is not a security boundary in Odoo.** The exemption therefore lives in
process memory, which no request can reach: a thread-local set only by the
wizard, for the duration of one ``write``. Odoo handles a request on one thread
(and one greenlet under gevent), so the scope is exactly this call — and a
caller cannot name themselves into it, because there is nothing to name.
"""
import threading
from contextlib import contextmanager

from odoo import api, models

# Thread-local, NOT context: see the module docstring. A request cannot write
# here, which is the entire point.
_NC_TRANSFER_SCOPE = threading.local()


def _nc_transferring_ids():
    return getattr(_NC_TRANSFER_SCOPE, 'ids', frozenset())


@contextmanager
def nc_transfer_scope(asset_ids):
    """Mark ``asset_ids`` as mid-transfer for the duration of the block.

    Unions rather than replaces, and restores in ``finally``, so a nested or
    failed transfer cannot leave the guard disarmed for the next caller on this
    thread — a leaked scope would silently re-open the hole this replaces.
    """
    previous = _nc_transferring_ids()
    _NC_TRANSFER_SCOPE.ids = previous | frozenset(asset_ids)
    try:
        yield
    finally:
        _NC_TRANSFER_SCOPE.ids = previous


class AccountAsset(models.Model):
    _inherit = 'account.asset'

    @api.constrains('profile_id')
    def _check_profile_change(self):
        """OCA's guard, minus the assets this thread is transferring.

        Overriding by NAME rather than adding a second constraint: Odoo keys
        constraint methods on their name, so redefining it here replaces the
        OCA one for this model — and delegating to ``super()`` on the remainder
        keeps a bare edit refused with OCA's own message, so nobody has to
        maintain a second copy of that wording.
        """
        scope = _nc_transferring_ids()
        remainder = self.filtered(lambda asset: asset.id not in scope)
        if remainder:
            return super(AccountAsset, remainder)._check_profile_change()
        return None
