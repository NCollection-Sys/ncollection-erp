{
    'name': 'NCollection Branding',
    'version': '19.0.1.2.0',
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
    # base_setup: the Settings "About" view; http_routing: branded error
    # pages. Both are auto-installed core addons, always present.
    'depends': ['web', 'mail', 'base_setup', 'http_routing'],
    'data': [
        'views/webclient_templates.xml',
        'views/res_config_settings_views.xml',
        'views/http_error_templates.xml',
        'views/mail_layout_templates.xml',
        'data/res_company_data.xml',
        'data/system_parameters.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'ncollection_branding/static/src/scss/theme_colors.scss',
            'ncollection_branding/static/src/js/white_label.js',
            'ncollection_branding/static/src/xml/error_dialogs_patch.xml',
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
