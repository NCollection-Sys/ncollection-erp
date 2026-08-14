# -*- coding: utf-8 -*-
"""F4-T03: asset transfer — the flow OCA does not model.

``account_asset_management`` knows three verbs: register, depreciate, remove.
FPA §7 lists a fourth — **Asset Transfer** — and there is no OCA equivalent to
adopt, so this is the one genuinely native piece of the lifecycle.

WHAT A TRANSFER IS. Two independent things, which real charts change together
and which this wizard therefore does in one step:

1. **Organisational** — the asset moves to another department / cost centre.
   Only ``analytic_distribution`` changes. Future depreciation posts to the new
   dimension because ``_setup_move_line_data`` reads the distribution off the
   asset at posting time. **No journal entry**: nothing moved in the GL.

2. **Reclassification** — the asset moves to another profile, whose asset and
   accumulated-depreciation accounts differ. That IS a GL movement, and needs
   an entry:

       Dr  new asset account          gross cost
       Cr  old asset account          gross cost
       Dr  old accumulated depr.      accumulated to date
       Cr  new accumulated depr.      accumulated to date

THE INVARIANT THIS MUST NOT BREAK, and the one the tests assert directly:

    a transfer changes WHERE value sits, never HOW MUCH.

Net book value is unchanged, and **the P&L is not touched at all** — all four
legs are balance-sheet accounts, guaranteed by the account-type guard in
``models/asset_profile.py`` (both profiles' accounts are already constrained to
``asset_fixed``/``asset_non_current``). A transfer that touched P&L would show
up as a phantom gain or loss, which is precisely what disposal — a different
verb — is for.

WHAT A TRANSFER DELIBERATELY DOES NOT DO: it does not re-plan the depreciation
board. Moving a laptop from Sales to HR does not restart its useful life, so
``method``, ``method_number``, ``method_period`` and ``date_start`` are left
exactly as they are even when the destination profile specifies different ones.
Odoo's own ``profile_id`` onchange copies those fields on a NEW asset; this
writes ``profile_id`` directly, precisely to bypass it. Asserted by a test,
because "the schedule silently changed" is the kind of thing nobody notices
until a year-end.
"""
from odoo import api, fields, models
from odoo.exceptions import UserError

from ..models.asset import nc_transfer_scope


def _nc_depends_roots(method):
    """The root field names a compute method declares it depends on.

    Lifted to module level so its defensive branch can be tested directly —
    the branch is unreachable through `account.asset` today, and an untestable
    guard is the kind that turns out not to work when it is finally needed.

    `@api.depends` also accepts a single CALLABLE for dynamic dependency lists
    (odoo/orm/decorators.py); core uses that form on mail.thread's blacklist
    mixin and account's sequence mixin. No field on `account.asset` uses it,
    but `tuple(<function>)` raises TypeError — which would break
    `action_transfer` for EVERY asset rather than for the one new field. What
    cannot be read is skipped rather than crashed on.
    """
    raw = getattr(method, '_depends', ())
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return set()
    return {str(dep).split('.')[0] for dep in raw}


class NcollectionAssetTransfer(models.TransientModel):
    _name = 'ncollection.account.asset.transfer'
    # analytic.mixin, NOT a hand-rolled `fields.Json`. The distribution widget
    # is bound to the mixin's field (plus `analytic_precision` and the
    # `distribution_analytic_account_ids` search helper); a bare Json field
    # renders as a raw dict the user cannot edit. That exact mistake was caught
    # in review on #121 before it shipped, and it is the same field.
    _inherit = ['analytic.mixin']
    _description = 'NCollection Asset Transfer'

    # No `string=`: pylint-odoo W8113 flags a label Odoo already derives from
    # the field name, and this repo's gate fails on any finding beyond baseline.
    asset_id = fields.Many2one(
        'account.asset', required=True, ondelete='cascade',
        domain="[('state', 'in', ('open', 'draft')),"
               " ('company_id', 'in', allowed_company_ids)]")
    date = fields.Date(
        required=True, default=fields.Date.context_today,
        string='Transfer Date',
        help="Date of the reclassification entry, when one is needed.")
    profile_id = fields.Many2one(
        'account.asset.profile', string='New Asset Profile',
        help="Leave empty to keep the current profile — use this to move the "
             "asset between GL asset accounts. The depreciation SCHEDULE is "
             "never re-planned by a transfer.")
    # `analytic_distribution` comes from analytic.mixin above — not redeclared
    # here. Redeclaring it would drop the mixin's compute/search and the widget
    # binding with them; the "New Analytic Distribution" label is set on the
    # view instead, which is where a label belongs.
    journal_id = fields.Many2one(
        'account.journal',
        domain="[('type', '=', 'general'),"
               " ('company_id', 'in', allowed_company_ids)]",
        help="Journal for the reclassification entry. Defaults to the "
             "destination profile's journal.")
    note = fields.Char(string='Reason')

    # ---- state ----------------------------------------------------------

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        # Reached from the asset form's action, so honour the active record.
        if self.env.context.get('active_model') == 'account.asset':
            active_id = self.env.context.get('active_id')
            if active_id:
                values.setdefault('asset_id', active_id)
        return values

    def _nc_target_profile(self):
        """The profile the asset ends up on — the new one, or the current."""
        self.ensure_one()
        return self.profile_id or self.asset_id.profile_id

    def _nc_accumulated(self):
        """Accumulated depreciation actually IN the GL at the transfer date.

        Read off the asset sub-ledger rather than ``value_depreciated``: the
        latter includes lines that are merely PLANNED. Reclassifying a planned
        amount would move value the general ledger has never seen, leaving the
        two asset accounts permanently out of step with their sub-ledger.

        ``init_entry`` lines count. They are the accumulated depreciation of an
        asset migrated in from a previous system, for which Odoo generated no
        move by design — real GL balance, no Odoo document behind it.
        """
        self.ensure_one()
        total = 0.0
        for line in self.asset_id.depreciation_line_ids:
            # 'posted' and never 'all': a reclassification may only move value
            # the general ledger has actually recognised. See
            # models/asset_line.py — one definition, two callers, the
            # difference expressed as an argument rather than a second copy.
            if not line._nc_is_in_gl('posted'):
                continue
            if line.line_date and line.line_date > self.date:
                continue
            total += line.amount
        return total

    # ---- the flow -------------------------------------------------------

    def action_transfer(self):
        self.ensure_one()
        asset = self.asset_id
        if asset.state not in ('draft', 'open'):
            raise UserError(self.env._(
                "Asset %(asset)s is %(state)s. Only a draft or running asset "
                "can be transferred — a closed or removed asset has already "
                "left the balance sheet, and moving it between accounts would "
                "resurrect a value that is no longer there.",
                asset=asset.display_name, state=asset.state))
        if not self.profile_id and not self.analytic_distribution:
            raise UserError(self.env._(
                "Nothing to transfer: set a new profile, a new analytic "
                "distribution, or both."))
        self._nc_assert_company()

        move = self._nc_create_reclassification_move()

        # Write AFTER the entry: the entry describes the move OUT of the old
        # accounts, so it must be built while the asset still knows them.
        values = {}
        if self.profile_id:
            # Carry the asset's OWN terms across. Writing profile_id alone
            # re-derives all of them from the destination — see
            # _nc_profile_derived_fields for what that silently changes.
            values.update(self._nc_preserved_terms())
            values['profile_id'] = self.profile_id.id
        if self.analytic_distribution:
            # Explicit last: the whole point of an analytic transfer is to
            # CHANGE this one, so it must beat the preserved copy above.
            values['analytic_distribution'] = self.analytic_distribution
        # `nc_transfer_scope` narrows OCA's `_check_profile_change`, which
        # otherwise refuses any profile change on an asset that has posted
        # entries. See models/asset.py: the guard is right for a bare edit and
        # is NOT removed — it is scoped past the one asset whose GL position
        # the reclassification entry above has just moved to match.
        #
        # A THREAD-LOCAL, not a context key. The first version used
        # `with_context(nc_asset_transfer_ids=...)`, which any RPC caller can
        # forge verbatim (`service/model.py:call_kw` applies the caller's
        # context with no allowlist) — and OCA grants `account.asset` write to
        # group_account_user and group_account_invoice, well below this
        # wizard's manager-only gate. Caught in review; see models/asset.py.
        with nc_transfer_scope((asset.id,)):
            asset.write(values)

        body = self.env._(
            "Asset transferred on %(date)s.", date=self.date)
        if self.note:
            body += " " + self.env._("Reason: %(note)s", note=self.note)
        if move:
            body += " " + self.env._(
                "Reclassification entry: %(move)s", move=move.display_name)
        asset.message_post(body=body)

        if move:
            return {
                'type': 'ir.actions.act_window',
                'name': self.env._("Reclassification Entry"),
                'res_model': 'account.move',
                'res_id': move.id,
                'view_mode': 'form',
                'target': 'current',
            }
        return {'type': 'ir.actions.act_window_close'}

    # ---- company boundary -------------------------------------------------

    def _nc_assert_company(self):
        """Refuse a transfer that would cross a company boundary.

        Odoo's own `check_company=True` on `account.asset.profile_id` and
        `account.move.journal_id` already blocks most of this — but only on the
        RECLASSIFICATION path. An analytic-only transfer (no new profile, so no
        move) touches neither, so a genuine multi-company user could silently
        re-point another company's asset. Borrowed protection that the module
        neither states nor tests is the thing this repo's ledger is made of, so
        the boundary is asserted here and proved by a test, matching the
        precedent ncollection_account_budget set with its company ir.rules.
        """
        self.ensure_one()
        asset = self.asset_id
        if asset.company_id and asset.company_id not in self.env.companies:
            raise UserError(self.env._(
                "Asset %(asset)s belongs to %(owner)s, which is not among your "
                "allowed companies.",
                asset=asset.display_name, owner=asset.company_id.display_name))
        target = self._nc_target_profile()
        if target.company_id and target.company_id != asset.company_id:
            raise UserError(self.env._(
                "Asset profile %(profile)s belongs to %(owner)s but the asset "
                "belongs to %(asset_owner)s. A transfer moves an asset between "
                "accounts, never between companies.",
                profile=target.display_name,
                owner=target.company_id.display_name,
                asset_owner=asset.company_id.display_name))
        journal = self.journal_id or target.journal_id
        if journal and journal.company_id != asset.company_id:
            raise UserError(self.env._(
                "Journal %(journal)s belongs to %(owner)s but the asset "
                "belongs to %(asset_owner)s.",
                journal=journal.display_name,
                owner=journal.company_id.display_name,
                asset_owner=asset.company_id.display_name))

    # ---- preserving the asset's own terms --------------------------------

    def _nc_profile_derived_fields(self):
        """The fields OCA recomputes from ``profile_id`` — DISCOVERED, not
        listed.

        Every one of these is declared ``compute=..., store=True,
        readonly=False`` with a dependency on ``profile_id``, so a plain
        ``asset.write({'profile_id': ...})`` silently re-derives all of them
        from the destination profile. Measured on this tree, that is ELEVEN
        fields, and two of them change money rather than presentation:

            salvage_value          -> changes the depreciation BASE
            analytic_distribution  -> silently re-points future cost
            method, method_number, method_period, method_time,
            method_progress_factor, prorata, days_calc, use_leap_years,
            group_ids

        This was found by a test, not by reading: the first implementation
        wrote ``profile_id`` on its own and documented that a transfer "does
        not re-plan the board". It did — ``method_number`` went 5 -> 2 — and
        every other assertion in the suite still passed, because net book value
        and the reclassification entry are both correct either way. The damage
        would have surfaced a year later as a depreciation charge nobody
        authorised.

        Discovered at runtime rather than hardcoded so that a field ADDED
        upstream is covered by the next `make oca` instead of quietly falling
        out of the list. A hardcoded tuple would be a second copy of OCA's
        design, and this repo's ledger is mostly copies that drifted.
        """
        Asset = self.env['account.asset']
        candidates = {name: field for name, field in Asset._fields.items()
                      if field.compute and field.store and not field.readonly}

        def roots_of(field):
            method = (getattr(Asset, field.compute, None)
                      if isinstance(field.compute, str) else field.compute)
            return _nc_depends_roots(method)

        # TRANSITIVE, not one level. `method_end` is compute+store+writable and
        # declares `@api.depends("method_number")` — it never names profile_id,
        # yet method_number IS profile-derived, so a one-level scan missed it.
        # It is harmless today only because `_compute_method_end` no-ops when
        # method_number is set; that is luck in the current fixtures, not a
        # property this code establishes. Found in review.
        derived = set()
        frontier = {'profile_id'}
        while True:
            found = {name for name, field in candidates.items()
                     if name not in derived and roots_of(field) & frontier}
            if not found:
                return sorted(derived)
            derived |= found
            frontier |= found

    def _nc_preserved_terms(self):
        """``{field: write-value}`` for the asset's current terms.

        ``convert_to_write`` rather than the raw value: ``group_ids`` needs
        m2m commands and ``analytic_distribution`` needs a plain dict, and a
        raw recordset in a write dict fails in a way that looks like a data
        error rather than a type error.
        """
        self.ensure_one()
        asset = self.asset_id
        return {name: asset._fields[name].convert_to_write(asset[name], asset)
                for name in self._nc_profile_derived_fields()}

    def _nc_create_reclassification_move(self):
        """The GL entry, or ``False`` when the transfer is analytic-only.

        Returning ``False`` rather than posting a zero-value entry is the point:
        a department change is not a general-ledger event, and a journal full of
        balanced zero entries is noise that hides the reclassifications that
        DID move money.
        """
        self.ensure_one()
        asset = self.asset_id
        source = asset.profile_id
        target = self._nc_target_profile()
        if not self.profile_id or target == source:
            return False
        same_asset_account = target.account_asset_id == source.account_asset_id
        same_depr_account = (
            target.account_depreciation_id == source.account_depreciation_id)
        if same_asset_account and same_depr_account:
            # A different profile can still share both accounts (e.g. it only
            # differs by depreciation method). Nothing moved in the GL.
            return False

        gross = asset.purchase_value
        accumulated = self._nc_accumulated()
        currency = asset.company_id.currency_id
        distribution = self.analytic_distribution or asset.analytic_distribution

        lines = []
        if not currency.is_zero(gross) and not same_asset_account:
            lines += [
                self._nc_move_line(target.account_asset_id, gross, 0.0,
                                   distribution),
                self._nc_move_line(source.account_asset_id, 0.0, gross,
                                   distribution),
            ]
        if not currency.is_zero(accumulated) and not same_depr_account:
            lines += [
                self._nc_move_line(source.account_depreciation_id,
                                   accumulated, 0.0, distribution),
                self._nc_move_line(target.account_depreciation_id, 0.0,
                                   accumulated, distribution),
            ]
        if not lines:
            # Both accounts differ but both amounts are zero (a draft asset at
            # zero cost). Still nothing to post.
            return False

        journal = self.journal_id or target.journal_id
        # `allow_asset` is required by account_asset_management: it refuses any
        # move line carrying an `asset_id` created outside its own posting path
        # (models/account_move.py), which is a good guard and not one to work
        # around by omitting the link — the link is what makes this entry
        # traceable from the asset.
        move = self.env['account.move'].with_context(allow_asset=True).create({
            'journal_id': journal.id,
            'date': self.date,
            'company_id': asset.company_id.id,
            'ref': self.env._(
                "Asset transfer: %(asset)s", asset=asset.display_name),
            'line_ids': [(0, 0, values) for values in lines],
        })
        move.with_context(allow_asset=True).action_post()
        return move

    def _nc_move_line(self, account, debit, credit, distribution):
        self.ensure_one()
        return {
            'account_id': account.id,
            'name': self.note or self.env._(
                "Asset transfer: %(asset)s",
                asset=self.asset_id.display_name),
            'debit': debit,
            'credit': credit,
            'analytic_distribution': distribution,
            'asset_id': self.asset_id.id,
        }
