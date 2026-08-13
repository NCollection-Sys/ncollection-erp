{
    'name': 'NCollection MIS Templates: Balance Sheet & P&L',
    # 19.0.1.2.0 (#411): the Balance Sheet and P&L KPI expressions dropped
    # `expense_other`, so the template did not balance on a book using one.
    # The records are deliberately NOT noupdate, so `-u` reloads them — the
    # bump is the audit trail telling an operator that upgrade is a
    # correctness fix, not a no-op.
    'version': '19.0.1.2.0',
    'category': 'Accounting/Reporting',
    'summary': 'Ready-made Balance Sheet and Profit & Loss MIS report templates',
    'author': 'NCollection',
    'license': 'LGPL-3',
    'depends': ['mis_builder', 'account'],
    'data': [
        'data/mis_report_balance_sheet.xml',
        'data/mis_report_profit_and_loss.xml',
        'data/mis_report_instance.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
