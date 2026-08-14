# -*- coding: utf-8 -*-
# Full module documentation lives in README.rst and in each model's docstring.
# `description` is deprecated (pylint-odoo C8103) and the superfluous keys
# `data`/`installable`/`application` are omitted deliberately: they equal the
# Odoo defaults, and the last two new-module PRs in this repo (#101, #119) each
# added exactly +1 pylint finding by following this pattern.
{
    'name': 'NCollection AI',
    'summary': 'Tenant-side AI context injection (P5-T03), the '
               'natural-language search-domain mapper (P5-T05, off by '
               'default) and the assistant chat widget (P5-T06)',
    'version': '19.0.1.2.0',
    'category': 'Productivity',
    'author': 'NCollection',
    'license': 'LGPL-3',
    # ncollection_core carries the P4-T01 aggregation engine this consumes.
    'depends': ['ncollection_core'],
    'data': [
        'security/ir.model.access.csv',
        'security/chat_security.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'ncollection_ai/static/src/chat/chat_widget.scss',
            'ncollection_ai/static/src/chat/chat_widget.js',
            'ncollection_ai/static/src/chat/chat_widget.xml',
        ],
    },
}
