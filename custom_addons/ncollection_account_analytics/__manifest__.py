# pylint: disable=manifest-required-author
# (C8101 wants the OCA as author; this is a proprietary NCollection module.)
{
    'name': 'NCollection Account Analytics',
    'version': '19.0.1.1.0',
    'category': 'Accounting/Accounting',
    'summary': 'Financial analytics (F4-T01): cost/profit centres on Odoo '
               'analytic plans, financial KPIs, budget variance and trend '
               'extrapolation — the native owner of financial computation '
               'named by ADR #15',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    # SELECTABLE IN A SUBSCRIPTION PLAN (#467). `application` is what
    # ncollection.subscription.plan.get_selectable_modules() filters on, so
    # until now not one native NCollection accounting module could be licensed
    # to a tenant at all: the picker offered only Odoo/OCA apps, ENTERPRISE
    # therefore named only those, and every ncollection_account_* module was
    # `uninstalled` in every tenant database. This flag is the whole fix — it
    # changes no behaviour inside the module, only whether an operator can
    # choose it. Modules with no menus of their own stay dependency-only
    # (ncollection_account_core), because offering them would imply a choice
    # that does nothing.
    'application': True,
    # FPA §7 assigns Cost Centers, Profit Centers, Financial KPIs, Forecasting
    # and Variance Analysis to this module, and lists Reports / Journal Entries /
    # Accounting Configuration under "Must Never Own". It therefore depends on:
    #   - account                  : Odoo's accounting engine, which also brings
    #                                `analytic` as a DIRECT dependency — the
    #                                dimension mechanism below costs no new pin.
    #   - ncollection_account_core : the shared financial base + the boundary
    #                                guard every ncollection_account_* sibling
    #                                sits behind. It in turn brings
    #                                ncollection_core, home of the aggregation
    #                                engine this module reads through.
    #
    # NO OCA DEPENDENCY, deliberately (oca-scout, #120). OCA/account-analytic's
    # 19.0 branch is document-integration glue (purchase/sale/stock/POS auto-fill
    # of `analytic_distribution`) and models no cost- or profit-centre concept;
    # nothing in OCA provides financial KPI computation, variance or forecasting.
    # The closest matches are the mis_builder family — which ADR #15 / #117 plan
    # to RETIRE, and this module is named as their replacement. Deepening that
    # dependency here would work against the ticket's own purpose.
    'depends': ['account', 'ncollection_account_core'],
    'data': [
        'security/ir.model.access.csv',
        'data/analytic_plans.xml',
        'data/financial_kpi.xml',
        'views/analytics_views.xml',
    ],
}
