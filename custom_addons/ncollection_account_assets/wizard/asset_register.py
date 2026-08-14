# -*- coding: utf-8 -*-
"""F4-T03: the Asset Register, on the F2 engine.

The acceptance criterion says "Asset register report **via F2 engine**", and FPA
reserves reporting for ``ncollection_account_reports``. ``account_asset_management``
ships its own XLSX report and wizard; both are deliberately bypassed, exactly as
``ncollection_mis_templates`` consumes ``mis_builder``'s engine rather than its
UI. One report engine means one set of filters, one PDF template, one XLSX
path, and one drill-down — the alternative is a second reporting surface that
looks almost the same and diverges quietly (#315 shipped a bug that lived
entirely in the gap between two renderers).

SOURCE OF TRUTH: the asset sub-ledger (``account.asset.line``), not
``account.move.line``. That is not the lazy choice, it is the correct one:

* an asset migrated in from a previous system carries its accumulated
  depreciation as ``init_entry`` lines, for which Odoo generates **no move at
  all** by design. A register built from journal items would silently report
  such an asset at its full gross value with zero accumulated depreciation —
  overstating net book value by exactly the amount already written off.
* the register's job is to explain a balance-sheet number asset by asset. The
  GL knows the total; only the sub-ledger knows the split.

``target_move`` is still honoured through ``move_id.state``, so the register ties
back to whichever GL view the user asked for.

COLUMN KEYS. The engine's line model has a fixed field set, so the register maps
onto existing keys rather than adding fields nothing else uses, and relabels them
— the same approach #121's Budget-vs-Actual took, and for the same reason: the
engine, the PDF and the XLSX all read the keys, so a report cannot end up named
differently in three renderers.

    debit           -> Purchase Value      (gross cost)
    opening_balance -> Opening NBV         (at date_from)
    balance         -> Depreciation        (charged within the window)
    credit          -> Accumulated Depr.   (to date_to)
    closing_balance -> Closing NBV         (at date_to)

and the identity the totals row must satisfy, asserted by a test:

    Closing NBV = Purchase Value - Accumulated Depreciation
    Closing NBV = Opening NBV - Depreciation      (absent additions/disposals)
"""
from odoo import fields, models


class NcollectionAssetRegister(models.TransientModel):
    _name = 'ncollection.account.report.asset.register'
    _inherit = ['ncollection.account.report']
    _description = 'NCollection Asset Register'

    profile_ids = fields.Many2many(
        'account.asset.profile', string='Asset Profiles',
        # EXPLICIT relation name. Odoo derives one from both table names
        # (`sorted([a, b]) + '_rel'`); here that is
        # `account_asset_profile_ncollection_account_report_asset_register_rel`
        # at 67 characters, over Postgres' 63-char identifier cap — the module
        # would not install at all. Same wall #121 hit and the same fix;
        # ncollection_account_reports/tests/test_identifier_limits.py records
        # F2-T08 hitting it too.
        relation='nc_asset_register_profile_rel',
        column1='wizard_id', column2='profile_id',
        help="Leave empty to include every profile.")
    group_ids = fields.Many2many(
        'account.asset.group', string='Asset Groups',
        relation='nc_asset_register_group_rel',
        column1='wizard_id', column2='group_id',
        help="Leave empty to include every group.")
    include_removed = fields.Boolean(
        string='Include Disposed Assets', default=True,
        help="Disposed assets are included by default: an asset sold during "
             "the window still explains part of the movement, and hiding it "
             "makes the register stop reconciling to the balance sheet.")

    # ---- engine contract -------------------------------------------------

    def _nc_report_title(self):
        return self.env._("Asset Register")

    def _nc_report_action_ref(self):
        return 'ncollection_account_assets.action_report_asset_register'

    def _nc_list_view_ref(self):
        return 'ncollection_account_assets.view_report_line_asset_register_list'

    def _nc_columns(self):
        return [
            {'key': 'label', 'label': self.env._("Asset"), 'type': 'char'},
            {'key': 'debit', 'label': self.env._("Purchase Value"),
             'type': 'monetary'},
            {'key': 'opening_balance', 'label': self.env._("Opening NBV"),
             'type': 'monetary'},
            {'key': 'balance', 'label': self.env._("Depreciation"),
             'type': 'monetary'},
            {'key': 'credit', 'label': self.env._("Accumulated Depr."),
             'type': 'monetary'},
            {'key': 'closing_balance', 'label': self.env._("Closing NBV"),
             'type': 'monetary'},
        ]

    # ---- figures ---------------------------------------------------------

    def _nc_asset_domain(self):
        """Which assets this run reports on."""
        self.ensure_one()
        domain = [
            ('company_id', '=', self.company_id.id),
            # An asset acquired AFTER the window has no business in it. One
            # acquired before is in scope whether or not it moved.
            ('date_start', '<=', self.date_to),
        ]
        if self.profile_ids:
            domain.append(('profile_id', 'in', self.profile_ids.ids))
        if self.group_ids:
            domain.append(('group_ids', 'in', self.group_ids.ids))
        if not self.include_removed:
            domain.append(('state', '!=', 'removed'))
        return domain

    def _nc_line_counts(self, line):
        """Is this depreciation line IN the general ledger for this run?

        ``init_entry`` lines count unconditionally — they represent GL balance
        brought forward from a previous system with no Odoo move behind them, so
        keying on ``move_id`` would drop exactly the assets whose history
        matters most.
        """
        self.ensure_one()
        return line._nc_is_in_gl(self.target_move)

    def _nc_asset_figures(self, asset):
        """``(gross, opening_nbv, period_depr, accumulated, closing_nbv)``."""
        self.ensure_one()
        gross = asset.purchase_value
        before = period = 0.0
        for line in asset.depreciation_line_ids:
            if not self._nc_line_counts(line) or not line.line_date:
                continue
            if line.line_date < self.date_from:
                before += line.amount
            elif line.line_date <= self.date_to:
                period += line.amount
        accumulated = before + period
        # An asset acquired INSIDE the window has no opening balance — it was
        # not on the balance sheet on date_from. Reporting `gross - before`
        # there would invent an opening value and make the movement column stop
        # explaining the difference between the two NBVs.
        opening = (gross - before) if asset.date_start < self.date_from else 0.0
        return gross, opening, period, accumulated, gross - accumulated

    def _nc_compute_lines(self):
        """One row per asset, then a total."""
        self.ensure_one()
        assets = self.env['account.asset'].search(
            self._nc_asset_domain(), order='code, name, id')
        rows = []
        totals = [0.0, 0.0, 0.0, 0.0, 0.0]
        for asset in assets:
            figures = self._nc_asset_figures(asset)
            totals = [running + value
                      for running, value in zip(totals, figures)]
            gross, opening, period, accumulated, closing = figures
            # NO `account_id` ON PURPOSE, and this comment is the guard.
            # Every sibling F2 report's list view wires an `action_drill_down`
            # button, and copying that pattern here would be wrong: the generic
            # drill-down scopes journal items by a row's single account, and an
            # asset row spans two or three (gross, accumulated, expense). With
            # `account_id` False the domain silently degrades to "no account
            # filter" — every unrelated journal item matching the other
            # filters. Nothing errors, nothing fails to balance, only the
            # attribution is wrong: the exact failure this module's
            # account-type guard exists to eliminate elsewhere. A drill-down
            # here must filter on `asset_id`, not on an account.
            rows.append({
                'label': asset.display_name,
                'debit': gross,
                'opening_balance': opening,
                'balance': period,
                'credit': accumulated,
                'closing_balance': closing,
                'level': 1,
            })
        if not rows:
            return []
        gross, opening, period, accumulated, closing = totals
        rows.append({
            'label': self.env._("TOTAL"),
            'debit': gross,
            'opening_balance': opening,
            'balance': period,
            'credit': accumulated,
            'closing_balance': closing,
            'level': 0,
        })
        return rows

    def _nc_totals(self):
        """``(gross, opening_nbv, depreciation, accumulated, closing_nbv)``.

        The service surface — for tests, and for an F3 dashboard that wants the
        headline without parsing rows. Same shape ``ncollection_account_budget``
        exposes, so a caller learns one convention.
        """
        self.ensure_one()
        assets = self.env['account.asset'].search(self._nc_asset_domain())
        totals = [0.0, 0.0, 0.0, 0.0, 0.0]
        for asset in assets:
            totals = [running + value for running, value
                      in zip(totals, self._nc_asset_figures(asset))]
        return tuple(totals)
