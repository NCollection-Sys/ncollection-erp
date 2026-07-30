==========================
NCollection Account Reports
==========================

**Task:** F2-T01 · **Architecture:** ``FINANCIAL_PLATFORM_ARCHITECTURE.md`` §7/§10

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

What this does NOT own
======================

- The **reports** themselves (GL, TB, BS, P&L …) — those are F2-T02+ on this engine.
- Any **accounting logic** — posting, tax, reconciliation stay Odoo's (FPA §6);
  the engine reads via ``_read_group``, one aggregate query (the < 2s target).
- Dashboards / KPIs — ``ncollection_account_dashboard`` / ``_analytics``.

Dependencies
============

``account`` (the engine it reads) · ``ncollection_account_core`` (shared base).
XLSX uses ``xlsxwriter`` (already in Odoo 19's requirements); PDF uses native
``qweb-pdf``. **No OCA dependency** (FPA §7 names exactly these two).
