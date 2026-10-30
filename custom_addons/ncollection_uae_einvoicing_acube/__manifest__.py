# pylint: disable=manifest-required-author
# (C8101 wants the OCA as author; this is a proprietary NCollection module.)
{
    'name': 'NCollection UAE E-Invoicing: A-Cube adapter',
    'version': '19.0.0.1.0',
    'category': 'Accounting/Localizations',
    'summary': 'ASP adapter for A-Cube (MoF accreditation 188961), built against its published Gov AE OpenAPI '
    'contract. Behind the provider-neutral contract of ncollection_uae_einvoicing; NOT yet proven against the '
    'provider sandbox (no credential), so not for production.',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    # ncollection_uae_einvoicing: the engine and its adapter contract. The engine never imports this module.
    # NO pip dependency: `requests` and `lxml` are in the Odoo image.
    'depends': ['ncollection_uae_einvoicing'],
    'data': [
        'views/provider_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
