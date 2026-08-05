{
    'name': 'NCollection Account Dashboard',
    'version': '19.0.1.0.0',
    'category': 'Accounting/Dashboard',
    'summary': 'Finance, Accountant and Cash financial dashboards '
               '(presentation only — consumes the executive report services)',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    # ncollection_account_reports: the F2-T08 executive report SERVICES this
    # module consumes for every figure (never re-computed here — FPA §7
    # "Must Never Own: Report Generation, Accounting Rules"). ncollection_branding:
    # the UI-T02 OWL component library + design tokens the dashboards render with.
    'depends': ['ncollection_account_reports', 'ncollection_branding'],
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
        ],
    },
}
