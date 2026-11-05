=========================================
NCollection Bank Reconciliation - Sales Orders
=========================================

The **Sales** action of the bank matching workspace (``ncollection_bank_reconciliation``), installed automatically
when Sales is installed.

A sales order is not a receivable. This module never reconciles a bank transaction with an order and never books
revenue because a transaction names one. It finds the customer's confirmed orders, creates the invoice with Odoo's own
invoicing (regular, or the native down payment), posts it only when the user chooses to, and adds the posted invoice's
receivable item to the transaction, where the normal reconciliation validation applies.

Everything runs as the user (no sudo); a down payment needs the Sales user right the native wizard checks; an order that
already has a draft invoice, or has nothing left to invoice, is refused.

Documentation: ``docs/markdown/BANK_RECONCILIATION.md`` section 10a.

License: LGPL-3.
