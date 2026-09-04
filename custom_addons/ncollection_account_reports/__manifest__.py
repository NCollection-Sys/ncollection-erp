# pylint: disable=manifest-required-author
# (C8101 wants the OCA as author; this is a proprietary NCollection module.)
{
    'name': 'NCollection Account Reports',
    # 19.0.1.4.0 (#315/F2-T09): Department / Cost Centre / Profit Centre
    # analysis. New models + views + ACL rows + a new dependency, so `-u` is
    # what applies it to an existing tenant database.
    'version': '19.0.1.5.0',
    'category': 'Accounting/Accounting',
    'summary': 'Native financial report engine: definition, filters, drill-down, '
               'PDF + XLSX export (F2-T01) — the permanent replacement for the '
               'OCA tactical reporting bootstrap (ADR #15)',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    # SELECTABLE IN A SUBSCRIPTION PLAN (#467). `application` is what
    # ncollection.subscription.plan.get_selectable_modules() filters on, so
    # until now not one native NCollection accounting module could be licensed
    # to a tenant at all: the picker offered only Odoo/OCA apps, ENTERPRISE
    # therefore named only those, and every ncollection_account_* module was
    # `uninstalled` in every tenant database. This flag is the whole fix — it
    # changes no behaviour inside the module, only whether an operator can
    # choose it. Modules with no menus of their own stay dependency-only
    # (ncollection_account_core), because offering them would imply a choice
    # that does nothing.
    'application': True,
    # FPA §7: ncollection_account_reports depends ONLY on Odoo's accounting
    # engine + the shared financial base. XLSX export uses `xlsxwriter` (a plain
    # Python lib already shipped in Odoo 19's requirements) and PDF uses native
    # qweb-pdf — so NO OCA dependency is added (oca-scout: BUILD; adopting OCA
    # report_xlsx would bake an AGPL dep into the module meant to OUTLIVE the
    # ADR #15 sunset). Odoo owns posting/tax; this module only READS and renders.
    # F2-T09 (#315) adds ncollection_account_analytics. The three dimension
    # reports group by the analytic PLANS that module seeds, so they are
    # meaningless without it — a hard dependency is the honest encoding: the
    # menus exist exactly when the dimension does. The direction is acyclic by
    # construction, because FPA §7 forbids the analytics module from owning
    # reports, so it can never depend back on this one.
    'depends': ['account', 'ncollection_account_core',
                'ncollection_account_analytics'],
    'data': [
        'security/ir.model.access.csv',
        'security/report_security.xml',
        'report/report_templates.xml',
        'views/account_report_views.xml',
    ],
}
