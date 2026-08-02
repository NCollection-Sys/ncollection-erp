# -*- coding: utf-8 -*-
# pylint: disable=manifest-required-author
# (C8101 wants 'Odoo Community Association (OCA)' as author; this is a
#  proprietary NCollection module, not an OCA submission.)
{
    'name': 'NCollection Auth Hardening',
    # 1.2.0 (#261): auth-log retention becomes two-stage — minimise at 180d,
    # delete at 400d. The new parameter is seeded by migrations/19.0.1.2.0/,
    # NOT by the data file: its <function> lives in <data noupdate="1">, and
    # Odoo skips those on upgrade (convert.py _tag_function: `if self.noupdate
    # and self.mode != 'init': return`). An earlier version of this comment
    # claimed `-u` applies it — it does not, and a tenant with a customised
    # retention above 400 would have had the purge refuse on every run.
    'version': '19.0.1.2.0',
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
        'data/auth_cron.xml',
    ],
}
