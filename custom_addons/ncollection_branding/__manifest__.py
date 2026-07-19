{
    'name': 'NCollection Branding',
    'version': '19.0.1.1.0',
    'category': 'Theme/Customization',
    'summary': 'NCollection corporate branding: logo, colors, favicon',
    'description': """
NCollection Branding
=====================
Applies NCollection corporate identity across the Odoo backend:

* Custom logo and favicon
* Custom browser title
* Theme color palette (primary, secondary, silver, background, text)
""",
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    'depends': ['web', 'mail'],
    'data': [
        'views/webclient_templates.xml',
        'views/mail_layout_templates.xml',
        'data/res_company_data.xml',
        'data/system_parameters.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'ncollection_branding/static/src/scss/theme_colors.scss',
        ],
        'web.assets_frontend': [
            'ncollection_branding/static/src/scss/theme_colors.scss',
        ],
    },
    'images': ['static/description/icon.png'],
    'installable': True,
    'application': False,
    'auto_install': False,
}
