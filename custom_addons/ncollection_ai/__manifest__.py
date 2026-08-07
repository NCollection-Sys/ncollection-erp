# -*- coding: utf-8 -*-
{
    'name': 'NCollection AI',
    'summary': 'Tenant-side AI context injection (P5-T03)',
    'description': """
Builds tenant-scoped context for LLM prompts from ERP aggregations, sanitises
PII before it leaves the database, and talks to the AI gateway satellite over
HTTP.

Runs on TENANT databases only (DELIVERABLE_1_SYSTEM_DESIGN.md:244). The gateway
that performs the actual outbound call is a separate satellite container holding
no database credentials — see satellites/ai_gateway/README.md.
""",
    'version': '19.0.1.0.0',
    'category': 'Productivity',
    'author': 'NCollection',
    'license': 'LGPL-3',
    # ncollection_core carries the P4-T01 aggregation engine this consumes.
    'depends': ['ncollection_core'],
    'data': [],
    'installable': True,
    'application': False,
}
