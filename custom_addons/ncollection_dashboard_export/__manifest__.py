# pylint: disable=manifest-required-author
# (proprietary NCollection module — same exemption the other ncollection_* modules carry.)
{
    'name': 'NCollection Dashboard Export',
    'version': '19.0.1.0.0',
    'category': 'Hidden',
    'summary': 'An Export (Excel / PDF) button beside Share on the Dashboards page (/atlas/dashboards)',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    # The Dashboards page is Odoo's own Spreadsheet Dashboards app (`spreadsheet_dashboard`, path `dashboards`), where
    # Share already exists. This module adds Export next to it and nothing else: no model of its own, no ACL (it reads
    # the dashboard as the user), one authenticated route. auto_install: every tenant that has the Dashboards page
    # gets the button, with no plan entry.
    'depends': ['spreadsheet_dashboard', 'ncollection_core'],
    'data': [],
    'assets': {
        # the dashboard action and its Share button live in this bundle, so the extension must too
        'spreadsheet.o_spreadsheet': [
            'ncollection_dashboard_export/static/src/export/**/*.js',
            'ncollection_dashboard_export/static/src/export/**/*.xml',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': True,
}
