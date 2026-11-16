==========================================
NCollection Bank & Cash Journal Operations
==========================================

Every bank and cash journal card of the Accounting dashboard, existing or created later, becomes an operations
centre:

* **Current Balance** — the posted ledger balance of the journal's account (no reconciliation needed).
* **Pending Balance** — what posted receipts and payments still have on an outstanding account, incoming and outgoing
  shown apart; click it for the list.
* **Transactions** — the complete history: payments, receipts, transfers, bank transactions and other entries, one row
  per operation, with a running balance.
* **Payment · Receipt** — a short form (Type Customer / Supplier / Account, Payee, Description, Payment or Receipt
  Method); the journal and the direction come from the card and are enforced by the server.
* **Internal Transfer** — between two bank / cash journals of the company, on the company's Internal Transfer account.
* **Reconcile** — the existing matching workspace, for this journal.
* **Mark as Paid** — on an In Process payment or receipt: an Odoo statement line moves the amount from the outstanding
  account to the journal's account and is reconciled with the payment. A bank file imported later with the same
  amount is recognised instead of being added twice.

No accounting engine is added: Odoo's payments, statement lines and ``reconcile()`` do the bookkeeping.
Design and limits: ``docs/markdown/JOURNAL_OPERATIONS.md``.
