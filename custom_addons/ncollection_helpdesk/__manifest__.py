# pylint: disable=manifest-required-author
# (C8101 wants the OCA as author; this is a proprietary NCollection module —
# same exemption ncollection_billing and ncollection_saas already carry.)
{
    'name': 'NCollection Helpdesk SLA',
    'version': '19.0.1.0.0',
    'category': 'Services/Helpdesk',
    'summary': 'SLA response/resolution timers on top of OCA helpdesk_mgmt (P6-T03)',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    # helpdesk_mgmt: the ticket model, teams, stages and the portal
    #   submit/track pages this builds on (OCA, pinned in repos.yml).
    # helpdesk_mgmt_rating: CSAT on close — the last leg of P6-T03's
    #   acceptance loop. Depended on here so installing THIS module gives the
    #   whole submit -> resolve -> rate stack in one step, which is also what
    #   keeps the CI install list to a single entry.
    #
    # WHY THIS MODULE EXISTS AT ALL: helpdesk_mgmt_sla is not on OCA's 19.0
    # branch (PR #1012 open, two earlier attempts closed unmerged), so SLA
    # timers are the one part of the ticket that cannot be adopted. See the
    # decision record in docs/markdown/OCA_DEPENDENCIES.md.
    'depends': ['helpdesk_mgmt', 'helpdesk_mgmt_rating'],
    'data': [
        'security/ir.model.access.csv',
        'security/sla_security.xml',
        'data/sla_data.xml',
        'views/sla_policy_views.xml',
        'views/helpdesk_ticket_views.xml',
    ],
}
