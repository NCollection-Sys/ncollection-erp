# Third disposable seed (after bank_fixture.py and bank_fixture_gaps.py, database `uxbankweb` ONLY): data for the
# Sales Order Matching and drag-and-drop browser checks, on its own bank journal so the other suites' figures hold.
#   Sales Bank : three bank lines for one customer, and four confirmed orders
#     order A (1,000, to invoice)  - bank line "Payment <A>"         -> regular invoice, posted, matched in full
#     order B (2,000, to invoice)  - bank line "Advance <B>" of 600  -> 30 % down payment, posted, matched
#     order C (800, ALREADY invoiced and posted) - bank line "Payment <C>" -> the Sales action refuses a second invoice
from datetime import timedelta

from odoo import Command, fields

assert env.cr.dbname == 'uxbankweb', 'disposable database only'
company = env.company
D = fields.Date.today() - timedelta(days=3)
journal = env['account.journal'].create({'name': 'Sales Bank', 'type': 'bank', 'code': 'BSLS',
                                         'suspense_account_id': company.account_journal_suspense_account_id.id})
cust = env['res.partner'].create({'name': 'Sales Customer'})
pricelist = env['product.pricelist'].create({'name': 'AED (sales fixture)', 'currency_id': company.currency_id.id})
product = env['product.product'].create({'name': 'Consulting day (sales fixture)', 'type': 'service',
                                         'list_price': 1000.0, 'invoice_policy': 'order', 'taxes_id': [Command.clear()]})


def order(amount):
    so = env['sale.order'].create({'partner_id': cust.id, 'pricelist_id': pricelist.id, 'order_line': [Command.create({
        'product_id': product.id, 'product_uom_qty': 1, 'price_unit': amount, 'tax_ids': [Command.clear()]})]})
    so.action_confirm()
    return so


so_a, so_b, so_c = order(1000.0), order(2000.0), order(800.0)
inv_c = so_c._create_invoices(final=True)
inv_c.action_post()
Line = env['account.bank.statement.line']
Line.create({'journal_id': journal.id, 'date': D, 'payment_ref': 'Payment ' + so_a.name, 'partner_id': cust.id, 'amount': 1000.0})
Line.create({'journal_id': journal.id, 'date': D, 'payment_ref': 'Advance ' + so_b.name, 'partner_id': cust.id, 'amount': 600.0})
Line.create({'journal_id': journal.id, 'date': D, 'payment_ref': 'Payment ' + so_c.name, 'partner_id': cust.id, 'amount': 800.0})
env.cr.commit()
print('SALESFIX', so_a.name, so_b.name, so_c.name, inv_c.name, inv_c.amount_total)
