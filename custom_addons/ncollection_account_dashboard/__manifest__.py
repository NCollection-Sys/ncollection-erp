{
    'name': 'NCollection Account Dashboard',
    # 19.0.1.1.1 (#332): charts did not shrink on a window resize — a stale
    # Chart.js resize parked during the entry animation was drained on top of
    # the correct new size. Pure JS asset change: no models, columns, data or
    # security records, so nothing MUST be migrated. It still needs
    # `-u ncollection_account_dashboard` per tenant to take effect, because Odoo
    # serves a CACHED asset bundle and the browser keeps the old code until the
    # module is upgraded. There is no fleet-wide upgrade orchestrator yet
    # (ARCHITECTURE_DATA_PLATFORM.md §7 — Phase 2), so for already-provisioned
    # tenants that is currently a manual ops step. The version bump exists so
    # that orchestrator, when it lands, has a signal to detect.
    'version': '19.0.1.2.0',
    'category': 'Accounting/Dashboard',
    'summary': 'Finance, Accountant and Cash financial dashboards '
               '(presentation only — consumes the executive report services)',
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
    # ncollection_account_reports: the F2-T08 executive report SERVICES this
    # module consumes for every figure (never re-computed here — FPA §7
    # "Must Never Own: Report Generation, Accounting Rules"). ncollection_branding:
    # the UI-T02 OWL component library + design tokens the dashboards render with.
    # ncollection_core (#57): the P4-T01 aggregation engine + the P4-T02
    # ncollection.kpi operational-KPI service the department dashboards consume,
    # and the sales/hr/warehouse role groups their menus gate on. Already present
    # transitively (the CEO dashboard already reads the engine); made explicit now
    # that this module references core models directly.
    'depends': ['ncollection_account_reports', 'ncollection_branding', 'ncollection_core'],
    'data': [
        'views/dashboard_actions.xml',
        'views/dashboard_menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            # Chart.js ships with Odoo; the dashboard owns the chart lifecycle
            # (NcChartWrapper only frames a canvas). The UI-T02 components are
            # already bundled by ncollection_branding, so they import directly.
            'web/static/lib/Chart/Chart.js',
            'ncollection_account_dashboard/static/src/dashboard/dashboard.scss',
            'ncollection_account_dashboard/static/src/dashboard/dashboard_base.js',
            'ncollection_account_dashboard/static/src/dashboard/dashboard_base.xml',
            'ncollection_account_dashboard/static/src/dashboard/finance_dashboard.js',
            'ncollection_account_dashboard/static/src/dashboard/accountant_dashboard.js',
            'ncollection_account_dashboard/static/src/dashboard/cash_dashboard.js',
            'ncollection_account_dashboard/static/src/dashboard/ceo_dashboard.js',
            'ncollection_account_dashboard/static/src/dashboard/ceo_dashboard.xml',
            'ncollection_account_dashboard/static/src/dashboard/sales_dashboard.js',
            'ncollection_account_dashboard/static/src/dashboard/hr_dashboard.js',
            'ncollection_account_dashboard/static/src/dashboard/warehouse_dashboard.js',
        ],
    },
}
