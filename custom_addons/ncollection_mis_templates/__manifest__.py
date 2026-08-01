{
    'name': 'NCollection MIS Templates: Balance Sheet & P&L',
    'version': '19.0.1.1.0',
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
