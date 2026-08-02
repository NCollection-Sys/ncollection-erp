{
    'name': 'NCollection White-Label Reseller System',
    'version': '19.0.1.0.0',
    'category': 'NCollection/Platform',
    'summary': 'Partner reseller accounts: cascading branding, sub-tenant '
               'management, provisioning quotas, and revenue-share reporting',
    'description': """
NCollection White-Label Reseller System (P10-T09)
=================================================
Platform (admin-DB) feature that lets partner accounts resell NCollection under
their own brand:

* Reseller record linked to a res.partner, with brand defaults + quota + share.
* Sub-tenants stamped with their reseller (parent link on ncollection.tenant).
* Cascading branding: a reseller's brand is pushed to each sub-tenant at
  provisioning via the sanctioned config-sync RPC channel (never a cross-DB
  read), applied override-if-default on the tenant's res.company.
* Partner-scoped provisioning quota enforced at the ORM (create) layer.
* Revenue-share reporting aggregated from admin-DB subscription/billing data.

Two-layer safe: all reseller state lives in the admin DB; brand values reach
tenant databases only through ncollection_saas' per-tenant-keyed config-sync.
""",
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    'depends': ['ncollection_saas', 'ncollection_billing', 'ncollection_branding'],
    'data': [
        'security/reseller_groups.xml',
        'security/reseller_security.xml',
        'security/ir.model.access.csv',
        'views/reseller_views.xml',
        'views/revenue_share_views.xml',
        'views/reseller_menus.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
