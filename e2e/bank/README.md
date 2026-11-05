# Bank Transactions & Matching — browser acceptance (headless Playwright)

Drives a **disposable** Odoo (database `uxbankweb`, port 8169) built by `fixtures/run_bank_web.sh`
(`OCA_DIR` must contain `account-reconcile` and `bank-statement-import`; `COMPOSE_DIR` = the checkout running the stack).

* `acceptance.js` — 17 checks: the real Accounting dashboard card (Upload, Transactions, To Reconcile, Statements, New),
  journal-scoped workspace, a match and an undo done in the UI and read back through the ORM, a manual transaction,
  Upload with preview and a refused duplicate re-upload, the cash journal, CEO read-only (UI and server), the
  accountant, Arabic RTL desktop / 375 px, English 375 px (the list must use the phone width).
* `concurrency.py` — two real database sessions racing for one transaction (`odoo shell`, disposable DB only).
* Shares `../dashboards/lib.js` (Playwright from `D:/Projects/ncollection-erp/e2e/node_modules`, Edge headless).
