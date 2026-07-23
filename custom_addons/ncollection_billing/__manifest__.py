# pylint: disable=manifest-required-author
{
    'name': 'NCollection Billing',
    'version': '19.0.2.0.0',
    'category': 'Services/SaaS',
    'summary': 'Subscription billing — invoices, UAE VAT, proration, Stripe collection (P2-T11/T13)',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    # ncollection_subscription: the subscription/tenant/plan models we bill.
    # account: Odoo's accounting engine — the billing engine generates standard
    #   account.move invoices in the ADMIN DB (FINANCIAL_PLATFORM_ARCHITECTURE
    #   §4/§5: Odoo owns account.move/tax/payment, NCollection owns the workflow).
    # payment_stripe (P2-T13): Odoo core Stripe provider (pulls payment +
    #   account_payment). We CONFIGURE it (admin DB, test mode) and hook payment
    #   confirmation to extend the subscription — never a gateway from scratch.
    'depends': ['ncollection_subscription', 'account', 'payment_stripe'],
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
