# pylint: disable=manifest-required-author
# (proprietary NCollection module — same exemption the other ncollection_* modules carry.)
{
    'name': 'NCollection Invoice Amount Audit',
    'version': '19.0.1.0.0',
    'category': 'Accounting',
    'summary': 'Audit entry (old / new amounts, user, IP, time, company, invoice) whenever an invoice amount changes '
               '(P8-T05 / #81 acceptance criterion)',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    # WHY A NATIVE MODEL AND NOT A RULE ON OCA `auditlog` (ncollection_audit): auditlog on account.move.line overflows
    # recursion limit (79 nested write_full frames, #431), and on account.move alone logs nothing, because amount_total
    # is a stored COMPUTED field. This module compares the three stored amounts before and after the outermost ORM
    # write / create / unlink on invoices and invoice lines and records only a real net change. Design record:
    # docs/markdown/INVOICE_AMOUNT_AUDIT.md. In the release matrix (pass 2), in no plan, never `auto_install`.
    'depends': ['account', 'ncollection_core'],
    'data': [
        'security/ir.model.access.csv',
        'security/audit_rules.xml',
        'views/invoice_audit_views.xml',
    ],
}
