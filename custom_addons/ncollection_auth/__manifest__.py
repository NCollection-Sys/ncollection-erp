# -*- coding: utf-8 -*-
# pylint: disable=manifest-required-author
# (C8101 wants 'Odoo Community Association (OCA)' as author; this is a
#  proprietary NCollection module, not an OCA submission.)
{
    'name': 'NCollection Auth Hardening',
    'version': '19.0.1.0.0',
    'category': 'Hidden',
    'summary': 'Authentication audit log + hardened login/session defaults (P1-T19)',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    # auth_signup: core (reset-password flow hooks). auth_session_timeout:
    # OCA/server-auth pinned in repos.yml (inactivity logout).
    'depends': ['base', 'web', 'auth_signup', 'auth_session_timeout'],
    'data': [
        'security/ir.model.access.csv',
        'data/auth_params.xml',
    ],
}
