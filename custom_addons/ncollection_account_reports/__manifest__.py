# pylint: disable=manifest-required-author
# (C8101 wants the OCA as author; this is a proprietary NCollection module.)
{
    'name': 'NCollection Account Reports',
    # 19.0.1.1.0 (#115/F2-T05): partner-facing reports — Partner Ledger first.
    # New models + views + ACL rows, so `-u` is what applies it to an existing
    # tenant database.
    'version': '19.0.1.1.0',
    'category': 'Accounting/Accounting',
    'summary': 'Native financial report engine: definition, filters, drill-down, '
               'PDF + XLSX export (F2-T01) — the permanent replacement for the '
               'OCA tactical reporting bootstrap (ADR #15)',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    # FPA §7: ncollection_account_reports depends ONLY on Odoo's accounting
    # engine + the shared financial base. XLSX export uses `xlsxwriter` (a plain
    # Python lib already shipped in Odoo 19's requirements) and PDF uses native
    # qweb-pdf — so NO OCA dependency is added (oca-scout: BUILD; adopting OCA
    # report_xlsx would bake an AGPL dep into the module meant to OUTLIVE the
    # ADR #15 sunset). Odoo owns posting/tax; this module only READS and renders.
    'depends': ['account', 'ncollection_account_core'],
    'data': [
        'security/ir.model.access.csv',
        'security/report_security.xml',
        'report/report_templates.xml',
        'views/account_report_views.xml',
    ],
}
