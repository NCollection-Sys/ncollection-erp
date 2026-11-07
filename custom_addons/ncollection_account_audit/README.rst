NCollection Invoice Amount Audit
================================

The #81 / P8-T05 acceptance criterion: changing an invoice amount produces an audit entry with old / new values,
user, IP and timestamp. One immutable ``ncollection.invoice.audit`` row per net change of an invoice's amounts,
captured around the outermost ORM operation on an invoice or its lines.

In the release matrix (pass 2 on the three target databases); in no plan and never ``auto_install``. Design, limits, install and rollback:
``docs/markdown/INVOICE_AMOUNT_AUDIT.md``.
