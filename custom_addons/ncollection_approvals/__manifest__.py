# -*- coding: utf-8 -*-
{
    'name': 'NCollection Approval Workflows',
    'version': '19.0.1.0.0',
    'category': 'Productivity',
    'summary': 'Configurable approval workflows: sales-order threshold approval, '
               'two-level purchase approval, and CRM territory lead assignment',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    # P3-T07. Approval workflows layered onto Odoo's own sale/purchase/crm ERP
    # models via _inherit — Odoo core is never modified (Rule 1). The gate is a
    # mail.activity + a state field per the architecture (DELIVERABLE_1 P3-T07);
    # oca-scout confirmed BUILD over OCA tier-validation (a days-old 19.0 port,
    # not vendored, and the architecture prescribes this custom shape).
    #   - sale / purchase / crm : the tenant-side ERP models we extend.
    #   - sales_team            : the sale-manager/salesman groups we gate on
    #                             (transitive via sale/crm, declared explicitly).
    #   - mail                  : mail.activity + mail.thread (the approval task).
    'depends': ['sales_team', 'sale', 'purchase', 'crm', 'mail'],
    'data': [
        'security/approvals_groups.xml',
        'security/ir.model.access.csv',
        'views/res_company_views.xml',
        'views/sale_order_views.xml',
        'views/purchase_order_views.xml',
        'views/crm_lead_views.xml',
        'views/approval_territory_views.xml',
    ],
}
