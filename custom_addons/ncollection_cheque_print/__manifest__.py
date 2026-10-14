# pylint: disable=manifest-required-author
# (C8101 wants the OCA as author; this is a proprietary NCollection module.)
{
    'name': 'NCollection Cheque Print',
    'version': '19.0.1.0.0',
    'category': 'Accounting/Accounting',
    'summary': 'Print the variable data of an Odoo payment onto a pre-printed bank cheque: configurable templates, a '
               'simple drag-and-drop designer, exact-size PDF, printer calibration and an audited print history',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    # ENTITLED BY ACCOUNTING, NOT BY A PLAN LINE: it is a COMPANION module
    # (ncollection_subscription/models/companions.py). A tenant whose entitlement includes `account` gets this module
    # (new tenants at provisioning, existing ones through the install job); a tenant without `account` does not. It is
    # deliberately not offered in the plan picker and a plan line naming it is ignored, so it can never be a standalone
    # entitlement. `application` only lists it in Apps.
    # NO HOME TILE, on purpose: cheque printing is an action on an Accounting payment plus the layouts it needs, so its
    # menus live INSIDE the Accounting app (Accounting > Cheque Printing, and Print Cheque on a vendor payment), the
    # way budget, assets and analytics do. See docs/markdown/CHEQUE_PRINTING.md ("Where to find it").
    'application': True,
    # account                    : the payment (account.payment) is the ONLY financial source of truth; nothing here
    #                              keeps a payment of its own.
    # mail                       : the template and the print job keep a chatter / change tracking.
    # ncollection_core           : the tenant-side licence machinery and role groups.
    # ncollection_account_core   : the shared financial base AND the shared amount-in-words service
    #                              (ncollection.amount.words), so the wording is one implementation for every document.
    # ncollection_branding       : the design tokens the template designer is styled with.
    # No OCA dependency and no pip dependency: the PDF is drawn with reportlab (already in the Odoo image) and the
    # Arabic shaping lives in models/text_shaping.py. See docs/markdown/CHEQUE_PRINTING.md.
    'depends': ['account', 'mail', 'ncollection_core', 'ncollection_account_core', 'ncollection_branding'],
    'data': [
        'security/ir.model.access.csv',
        'security/cheque_security.xml',
        'data/printer_profile_data.xml',
        'data/cheque_template_data.xml',
        'wizard/print_wizard_views.xml',
        'wizard/reason_wizard_views.xml',
        'views/template_views.xml',
        'views/printer_profile_views.xml',
        'views/print_job_views.xml',
        'views/account_payment_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'ncollection_cheque_print/static/src/designer/designer.scss',
            'ncollection_cheque_print/static/src/designer/designer.js',
            'ncollection_cheque_print/static/src/designer/designer.xml',
            'ncollection_cheque_print/static/src/job_list/job_list.js',
        ],
    },
}
