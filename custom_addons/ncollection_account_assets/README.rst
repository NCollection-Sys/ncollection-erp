==========================
NCollection Account Assets
==========================

Fixed assets for the NCollection platform (F4-T03, issue #122): registration,
depreciation, disposal, transfer, and the Asset Register report.

Adopt + wrap
============

Unlike ``ncollection_account_analytics`` (#120) and ``ncollection_account_budget``
(#121), which both concluded BUILD-CUSTOM, this module **adopts an OCA engine**
and wraps it.

Odoo 19 Community ships no fixed assets at all — ``account_asset`` moved to
Enterprise, verified against the running image. OCA's
``account_asset_management`` (19.0, *Mature*) owns the expensive and
correctness-critical part: depreciation boards (linear, linear-limit,
degressive, degressive-linear, degressive-limit), posting each period through a
real ``account.move``, and disposal with plus-/min-value recognition.

Decisively, it inherits ``analytic.mixin`` on both ``account.asset`` and
``account.asset.profile`` — the same ``analytic_distribution`` mechanism #120
established. The single-FK analytic model that disqualified ``account_budget_oca``
in #121 does **not** reproduce here, which is why the two tickets reach opposite
conclusions.

This module owns the three things OCA does not provide.

1. The account-type guard
-------------------------

``models/asset_profile.py``. The engine's three profile accounts are
unconstrained ``Many2one('account.account')``. Two shipped reports depend on
them being typed a particular way, and **both fail silently**:

* **#114 cash flow** treats ``expense_depreciation`` as a non-cash add-back,
  added in Operating and removed from Investing — equal and opposite, so the
  statement still balances and still reconciles no matter how the accounts are
  typed. Only the Operating/Investing *split* is wrong.
* **#411's Balance Sheet / P&L maps** place ``expense_depreciation`` in
  Operating Expenses and ``asset_fixed`` in Non-Current Assets. A charge landing
  on ``expense_direct_cost`` moves into Cost of Sales and changes reported Gross
  Margin — which ``ncollection_account_analytics`` publishes as a KPI.

``cash_flow.py`` states that assumption in **prose**. This module turns it into
an enforced constraint, and a test asserts the two classifications still agree —
so neither side can drift alone and leave a guard protecting nothing.

2. Transfer
-----------

``wizard/asset_transfer.py``. OCA models register / depreciate / remove;
"transfer" is not one of its verbs and FPA §7 lists it.

The invariant: **a transfer changes where value sits, never how much.** An
organisational move changes only ``analytic_distribution`` and posts no entry.
A reclassification posts four balance-sheet legs — gross and accumulated both
moved — leaving net book value unchanged and the P&L untouched.

Two things this gets right that a naive implementation does not:

* OCA refuses any ``profile_id`` change on an asset with posted entries, and it
  is right to: a bare edit re-points future depreciation while past entries stay
  behind, and the sub-ledger diverges from the GL permanently. The guard is
  **narrowed, not removed** (``models/asset.py``), scoped by record id to the
  asset whose GL position the reclassification entry has just moved to match.
* Eleven asset fields are ``compute + store + readonly=False`` on ``profile_id``,
  so writing the profile alone silently re-derives all of them from the
  destination — including ``salvage_value``, which moves the depreciation
  **base**. The wizard preserves them, discovering the list at runtime so a
  field added upstream is covered by the next ``make oca``.

3. The Asset Register, on the F2 engine
---------------------------------------

``wizard/asset_register.py``. OCA ships its own XLSX report; FPA reserves
reporting for ``ncollection_account_reports`` and the acceptance criterion says
"via F2 engine" explicitly — the same relationship ``ncollection_mis_templates``
has with ``mis_builder``.

Columns: Asset · Purchase Value · Opening NBV · Depreciation · Accumulated ·
Closing NBV, with PDF and XLSX through the shared engine.

Its source of truth is the **asset sub-ledger**, not ``account.move.line``: an
asset migrated in from a previous system carries its accumulated depreciation as
``init_entry`` lines with no Odoo move at all, and a register built from journal
items would report it at full gross with zero accumulated — overstating net book
value by exactly the amount already written off. ``target_move`` is still
honoured through ``move_id.state``, so the register ties back to the GL.

Configuration
=============

Create an **Asset Profile** per category (Accounting → Assets). The guard will
refuse a profile whose accounts are typed wrongly, and the error names the
report that would have misreported.

Testing
=======

``make test m=ncollection_account_assets`` — 37 tests.

License
=======

LGPL-3, following ``ncollection_mis_templates``' precedent for a wrapper over an
AGPL-3 OCA module. That precedent is inherited rather than independently
settled; see ``docs/markdown/OCA_DEPENDENCIES.md``.
