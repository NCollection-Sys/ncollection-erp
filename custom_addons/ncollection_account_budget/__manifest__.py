# pylint: disable=manifest-required-author
# (C8101 wants the OCA as author; this is a proprietary NCollection module.)
{
    'name': 'NCollection Account Budget',
    'version': '19.0.1.1.0',
    'category': 'Accounting/Accounting',
    'summary': 'Budget planning, revision, approval and Budget-vs-Actual '
               'reporting (F4-T02)',
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
    # FPA §7 lists Budget Planning / Revision / Approval / Monitoring /
    # Budget vs Actual here, and — unlike ncollection_account_analytics — gives
    # this module no "Must Never Own: Reports", so the report ships here.
    # Depends on:
    #   - account                     : the engine that owns the actuals.
    #   - ncollection_account_core     : the shared financial base.
    #   - ncollection_account_reports  : the F2 report engine the acceptance
    #                                    names ("budget-vs-actual report via
    #                                    the F2 report engine"). It brings
    #                                    ncollection_account_analytics with it,
    #                                    which is where the Department / Cost
    #                                    Centre analytic plans live.
    # The direction is budget -> reports and never the reverse: an optional
    # module may gain a report, but the report engine must not require a budget.
    #
    # NO OCA DEPENDENCY (oca-scout, #121). Odoo 19 Community ships no budgeting
    # at all — account_budget moved to Enterprise, verified against the running
    # image. OCA's account_budget_oca models a budget line with a SINGLE
    # analytic_account_id, which cannot express FPA's "Department AND Cost
    # Center" filtering nor compose with the analytic_distribution model #120
    # established. mis_builder_budget is compatible but AGPL-3, installed
    # nowhere, and inside the #117 sunset — useful as a design reference for
    # pro-rata accumulation, not as a dependency. repos.yml unchanged.
    #   - mail                        : declared explicitly because the budget
    #                                   model inherits mail.thread directly. It
    #                                   arrives transitively through account
    #                                   today, which works right up until that
    #                                   chain changes.
    'depends': ['account', 'mail', 'ncollection_account_core',
                'ncollection_account_reports'],
    'data': [
        'security/ir.model.access.csv',
        'security/budget_security.xml',
        'report/budget_report_templates.xml',
        'views/budget_views.xml',
    ],
}
