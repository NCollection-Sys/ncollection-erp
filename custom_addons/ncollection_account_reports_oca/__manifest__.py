# pylint: disable=manifest-required-author
# (C8101 wants the OCA as author; this is a proprietary NCollection module.)
{
    'name': 'NCollection Account Reports - OCA engine adapter',
    'version': '19.0.1.0.0',
    'category': 'Accounting/Accounting',
    'summary': 'Lets the NCollection report viewer use the official OCA '
               'account_financial_report engine as the calculation source for a '
               'report (pilot: Trial Balance), without exposing any OCA screen.',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    # LGPL-3 like ncollection_mis_templates: this module only CALLS an AGPL-3 OCA
    # module (account_financial_report) through its public report model; it
    # contains none of its code. The boundary is deliberate - ncollection_account_reports
    # itself stays free of any OCA dependency, so a tenant without the OCA module
    # (or one that switches the source back) loses nothing.
    'license': 'LGPL-3',
    # SELECTABLE IN A SUBSCRIPTION PLAN (like ncollection_account_reports, #467): this is
    # how an operator licenses the pilot to a tenant. Ring 2 only admits models of
    # modules the plan lists; a dependency-only module could never be switched on.
    'application': True,
    'depends': ['ncollection_account_reports', 'account_financial_report'],
    'data': [
        'security/ir.model.access.csv',
        'security/security.xml',
        'report/report_trial_balance_oca.xml',
    ],
}
