{
    'name': 'NCollection Branding',
    'version': '19.0.1.7.0',
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
    # pages; auth_signup: the login page injects the reset-password / signup
    # links that P1-T14 relabels. All three are auto-installed core addons,
    # always present.
    # web_tour: DECLARED, not inherited (#472). models/res_users.py overrides
    # its `_compute_tour_enabled`, so web_tour must load BEFORE this module or
    # the super() call has nothing to reach. It is a core Odoo module that
    # `web` already pulls in — naming it adds no dependency, it fixes the order.
    'depends': ['web', 'web_tour', 'mail', 'base_setup', 'http_routing', 'auth_signup'],
    # #472: existing users keep whatever web_tour computed at ITS install, and
    # the compute only fires on user creation — so a fresh install needs this
    # to reach the admin account that already exists.
    'post_init_hook': 'post_init_hook',
    'data': [
        'views/webclient_templates.xml',
        'views/login_templates.xml',
        'views/branding_theme_templates.xml',
        'views/component_playground.xml',
        'views/res_config_settings_views.xml',
        'views/http_error_templates.xml',
        'views/mail_layout_templates.xml',
        'data/res_company_data.xml',
        'data/res_company_logo.xml',
        'data/system_parameters.xml',
    ],
    'assets': {
        'web.assets_backend': [
            # tokens.scss FIRST: the design-system token layer (:root semantic
            # tokens + back-compat aliases, UI-T01/#128) that every other sheet
            # consumes via var(--nc-*).
            'ncollection_branding/static/src/scss/tokens.scss',
            'ncollection_branding/static/src/scss/theme_colors.scss',
            # Shared OWL component library (UI-T02/#129) + its dev playground.
            'ncollection_branding/static/src/components/components.scss',
            'ncollection_branding/static/src/components/playground.scss',
            'ncollection_branding/static/src/components/components.js',
            'ncollection_branding/static/src/components/components.xml',
            'ncollection_branding/static/src/components/playground.js',
            'ncollection_branding/static/src/components/playground.xml',
            'ncollection_branding/static/src/js/white_label.js',
            'ncollection_branding/static/src/xml/error_dialogs_patch.xml',
        ],
        'web.assets_frontend': [
            'ncollection_branding/static/src/scss/tokens.scss',
            'ncollection_branding/static/src/scss/theme_colors.scss',
            'ncollection_branding/static/src/scss/login.scss',
        ],
    },
    'images': ['static/description/icon.png'],
    'installable': True,
    'application': False,
    'auto_install': False,
}
