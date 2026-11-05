===============================
NCollection Bank Reconciliation
===============================

Bank Transactions and Bank Matching for Odoo 19 Community.

Adopts the OCA reconciliation engine (``account_reconcile_oca``) and statement import
(``account_statement_import_*``: CAMT.053, CSV/XLSX, QIF) and adds what they lack:

* one reconciliation at a time per transaction (row lock, refused at once when held);
* no double reconciliation, no counterpart of another company, the server mirrors the
  workspace's Validate button;
* an audited undo that rebuilds the pending match from the state after the undo;
* strict import: preview of the exact file, exact bank account, active company, the
  journal the upload started from, duplicate fingerprints for files without a bank
  transaction id;
* the journal card: Upload, Transactions, To Reconcile, Statements, New;
* Arabic for the workspace and the import screens.

Every match is Odoo's own ``account.move.line.reconcile()``; nothing edits a residual.

Decision record, parity matrix and acceptance evidence: ``docs/markdown/BANK_RECONCILIATION.md``.
