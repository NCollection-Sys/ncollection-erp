==========================
NCollection Account Reports
==========================

**Tasks:** F2-T01 (engine) · F2-T02 (General Ledger + Trial Balance) ·
**Architecture:** ``FINANCIAL_PLATFORM_ARCHITECTURE.md`` §7/§10

The native financial **report engine** — the permanent replacement for the OCA
tactical reporting bootstrap (ADR #15), retired fleet-wide by F2-T07 (#117) once
the native reports reach parity. Odoo owns accounting (FPA §4/§6); this module
only **reads** ``account.move.line`` and renders — it never posts or computes tax
(enforced by the F1-T02 engine-boundary guard).

The engine (``ncollection.account.report``)
===========================================

An ``AbstractModel`` every report wizard inherits. It owns:

- **Common filters** (FPA §10): company, date range, journals, accounts,
  partners, posted/all — and ``_nc_move_line_domain()`` that turns them into an
  ``account.move.line`` domain.
- **Shared rendering**: on-screen native ``<list>``, QWeb **PDF** (``qweb-pdf``),
  and **XLSX** (``xlsxwriter`` — a plain Python lib already in Odoo 19; no OCA
  ``report_xlsx`` dependency, so nothing survives the ADR #15 sunset).
- **Drill-down**: every report line opens the journal items behind it, scoped to
  the report's filters + the line's account — Odoo record rules still apply.

A concrete report subclasses it and implements two methods::

    class MyReport(models.TransientModel):
        _name = 'ncollection.account.report.my'
        _inherit = ['ncollection.account.report']

        def _nc_columns(self):
            return [{'key': 'label', 'label': 'Account', 'type': 'char'},
                    {'key': 'balance', 'label': 'Balance', 'type': 'monetary'}]

        def _nc_compute_lines(self):
            # one aggregate _read_group over _nc_move_line_domain() -> rows
            ...

PDF and XLSX render generically from ``_nc_columns()``. The **on-screen native
list** (`ncollection.account.report.line`) covers the common financial-report
shape — ``account_id`` / ``partner_id`` / ``label`` / ``debit`` / ``credit`` /
``balance`` / ``level`` — which fits GL, Trial Balance, Partner Ledger and Aged
(drill-down works by account *and* partner). A report with a fundamentally
different shape (e.g. a hierarchical single-amount Balance Sheet / P&L) reuses
the engine's filters, compute contract, PDF, XLSX and drill-down, but provides
its own list presentation. ``level`` is a *render* hint (indent/bold), never a
sort key — rows always render in ``_nc_compute_lines()`` order across all three
channels (``_order = 'id'``).

Reference report
================

``ncollection.account.report.reference`` — a Trial-Balance-style per-account
debit/credit/balance summary — ships as the reference that proves the engine
end-to-end (filters → list → drill-down → PDF → XLSX, < 2s). *Accounting →
Reporting → NCollection Reports → Account Balances (reference).*

F2-T02 reports: General Ledger + Trial Balance
==============================================

Two native production reports on the engine, both computed from
``account.move.line`` (Odoo owns the numbers) — the Trial Balance via
``_read_group`` aggregates, the General Ledger over the line detail it lists.
*Accounting → Reporting → NCollection Reports → {Trial Balance, General Ledger}.*

**Opening balance (shared, ``_nc_opening_balances()``)** — the accounting-correct
opening used by BOTH reports. Accounts are partitioned by Odoo's own
carry-forward flag, ``account.account.include_initial_balance``: **balance-sheet
accounts** (flag True) carry all prior activity; **P&L accounts AND the auto
current-year-earnings account** (flag False) reset at the fiscal-year start. We
read that flag rather than hand-maintain an ``account_type`` list, so future
localisation types are classified correctly for free (archived accounts are kept
via ``active_test=False`` — they can still carry a balance). Prior fiscal years'
net P&L rolls into the current-year-earnings (``equity_unaffected``) account's
opening — the **affectation of results** — so the opening column balances, exactly
as OCA ``account_financial_report`` computes it. The fiscal-year boundary comes
from ``res.company.compute_fiscalyear_dates()`` — Odoo's own calendar. A report
period must lie within one fiscal year (``_nc_assert_single_fiscal_year`` raises
otherwise) — spanning a boundary can't reset P&L cleanly, and a hard error beats
a silently wrong number.

**Trial Balance** (``…trial.balance``) — per account: Opening · Debit · Credit ·
**Closing = Opening + Debit − Credit**, closed by a balanced Total row. Its own
list view adds the opening/closing columns to the shared line model.

**General Ledger** (``…general.ledger``) — per account, an *opening* row
(``is_initial``) carrying the opening balance, then the period's journal items in
date order with a **cumulative running balance**. Uses its own transient line
model (``…gl.line``: date / entry / journal / partner / running balance) with
per-line drill-down scoped to that line's account. Both reports' line models are
**per-user** (``ir.rule`` on ``create_uid``) — one user never sees another's run.

**The acceptance — reconciliation:** the TB *Closing* per account equals the GL
*ending running balance* per account (``test_gl_tb.py`` proves it for a
balance-sheet and a P&L account across a fiscal-year boundary).

Each report has its **own** ``ir.actions.report`` with a **unique**
``report_name`` (thin wrapper template ``t-call``-ing the shared body) and its own
``_nc_report_action_ref()``. Sharing one ``report_name`` is ambiguous — Odoo
resolves reports by name, so every wizard would render against the first action's
model. Add a report → add its wrapper template + override.

Balance Sheet & Profit and Loss (F2-T03)
========================================

Two more wizards on the same engine, plus a **comparison mixin**
(``ncollection.account.report.comparison``) supplying the FPA column set
*Current · Previous · Variance · Variance %*. The mixin is separate from the
engine on purpose: GL and Trial Balance have no comparison concept and are left
untouched. F2-T08 (#118, executive reports) is expected to inherit it rather
than reimplement the period arithmetic.

* **Balance Sheet** is a *position* — cumulative to the as-of date
  (``_nc_closing_balances``, mis' ``bale``). **Assets = Liabilities + Equity**
  holds only because the *Accumulated Earnings* bucket carries the net result
  Odoo computes rather than posts.
* **Profit & Loss** is a *flow* — period movement only (``_nc_period_balances``,
  mis' ``balp``).
* ``previous_period`` compares against a span of the **same length** immediately
  before the report; a fixed calendar step would compare unequal periods and
  make the variance meaningless. ``variance_pct`` divides by ``abs(previous)``
  so the sign tracks the real move, and yields ``0.0`` (never ``inf``) when the
  previous figure is zero.

**These two replace** ``ncollection_mis_templates``' ``mis_report_bs`` /
``mis_report_pl``. Their ``account_type`` groupings are a 1:1 transcription of
that module's KPI expressions, and ``tests/test_bs_pl.py`` holds an *independent*
transcription that must agree — so a silent edit to either fails the build. The
retirement itself (uninstalling ``mis_builder``) is **#117 [F2-T07]**, not this
task; both remain installed and in agreement until then.

Executive Reports (F2-T08)
==========================

Financial Summary, Revenue Analysis, Expense Analysis and Profitability — the
FPA §10 *Executive Reports* catalog. They **compose**: every figure comes from
the F2-T03 Balance Sheet / P&L services or the F2-T01 engine, reached by
spawning an in-memory sibling wizard (``executive_base.py``) that carries this
report's own filters.

``tests/test_executive_reports.py`` enforces that structurally: it parses the
executive modules with ``ast``, strips docstrings and comments, and FAILS if any
of them names ``account.move.line``, ``_read_group`` or ``_nc_filter_domain``.
A parallel arithmetic that drifts from the statements it summarises therefore
cannot be introduced quietly — and a comment claiming compliance cannot satisfy
the guard.

``_nc_service_figures()`` is the surface **#56 [P4-T03] CEO Dashboard** consumes:
a dashboard calls it instead of re-deriving anything, and ``_nc_compute_lines``
renders from the same dict, so the report and the dashboard cannot disagree.

⚠ **Spec status.** Only *Financial Summary* has a real FPA specification (L1870,
the eight KPIs). *Revenue Analysis*, *Expense Analysis* and *Profitability
Report* appear in the §10 catalog with **no specification section**; their shape
is DERIVED from §Cost Center Analysis (*Revenue · Expense · Profit · Margin*)
with the P&L's own buckets and subtotals. The derivation is recorded at the top
of each wizard — reconcile those files first if a real spec is written.

*Department Analysis* is deliberately **not** here: the FPA catalog lists it
under *Management Reports*, not Executive, and it needs an analytic/department
dimension that does not exist in the financial data model. Building it means
introducing that dimension — an architecture decision, tracked separately.

What this does NOT own
======================

- More **reports** (Partner Ledger, Aged, Cash Flow, VAT …) — later F2 tasks on
  this engine. GL + Trial Balance ship here (F2-T02), BS + P&L (F2-T03).
- Any **accounting logic** — posting, tax, reconciliation stay Odoo's (FPA §6);
  the engine reads via ``_read_group``, one aggregate query (the < 2s target).
- Dashboards / KPIs — ``ncollection_account_dashboard`` / ``_analytics``.

Dependencies
============

``account`` (the engine it reads) · ``ncollection_account_core`` (shared base).
XLSX uses ``xlsxwriter`` (already in Odoo 19's requirements); PDF uses native
``qweb-pdf``. **No OCA dependency** (FPA §7 names exactly these two).
