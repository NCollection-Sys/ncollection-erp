# pylint: disable=manifest-required-author
{
    'name': 'NCollection Billing',
    'version': '19.0.1.0.0',
    'category': 'Services/SaaS',
    'summary': 'Subscription billing engine — invoices, UAE VAT, proration (P2-T11)',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    # ncollection_subscription: the subscription/tenant/plan models we bill.
    # account: Odoo's accounting engine — the billing engine generates standard
    #   account.move invoices in the ADMIN DB (FINANCIAL_PLATFORM_ARCHITECTURE
    #   §4/§5: Odoo owns account.move/tax/payment, NCollection owns the workflow).
    'depends': ['ncollection_subscription', 'account'],
    # No new models — only extensions of account.move / subscription / tenant /
    # res.company — so no new ir.model.access rows are required.
    'data': [
        'views/subscription_billing_views.xml',
    ],
    # Ensures the platform company has a Chart of Accounts (loads Odoo's generic
    # template if none), the 5% VAT tax, and the subscription product — the
    # prerequisites for admin-DB invoicing.
    'post_init_hook': 'post_init_hook',
}
