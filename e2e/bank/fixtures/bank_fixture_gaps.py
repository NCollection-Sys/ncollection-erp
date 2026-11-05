# Second disposable seed (after bank_fixture.py, database `uxbankweb` ONLY): data for the gap-closure browser checks,
# kept OFF Bank Misr so the first suite's figures do not move.
#   Ahly Bank : internal-transfer leg, bank fee, early-discount payment, a two-payment batch deposit, two duplicates
#   Bank Misr : the other internal-transfer leg
#   EUR Bank  : a EUR receipt for a EUR invoice booked at a lower rate (realised gain 40.00 AED)
from datetime import timedelta

from odoo import Command, fields

assert env.cr.dbname == 'uxbankweb', 'disposable database only'
company = env.company
TODAY = fields.Date.today()
D, D_OLD = TODAY - timedelta(days=5), TODAY - timedelta(days=15)
eur = env.ref('base.EUR')
eur.active = True
Rate = env['res.currency.rate']
Rate.search([('currency_id', '=', eur.id), ('company_id', 'in', (False, company.id))]).unlink()
Rate.create([{'currency_id': eur.id, 'name': D_OLD, 'rate': 0.25, 'company_id': company.id},        # 4.00 AED
             {'currency_id': eur.id, 'name': D, 'rate': 1 / 4.4, 'company_id': company.id}])        # 4.40 AED
misr = env['account.journal'].search([('code', '=', 'BMSR')])
ahly = env['account.journal'].search([('code', '=', 'BAHL')])
eur_bank = env['account.journal'].create({'name': 'EUR Bank', 'type': 'bank', 'code': 'BEUR', 'currency_id': eur.id,
                                          'suspense_account_id': company.account_journal_suspense_account_id.id})
cust = env['res.partner'].search([('name', '=', 'Nile Customer')], limit=1)
Line = env['account.bank.statement.line']


def post(move_vals):
    move = env['account.move'].create(move_vals)
    move.action_post()
    return move


# multi-currency: EUR invoice on D_OLD (400.00 AED), EUR receipt on D (440.00 AED)
inv_eur = post({'move_type': 'out_invoice', 'partner_id': cust.id, 'invoice_date': D_OLD, 'date': D_OLD,
                'currency_id': eur.id, 'invoice_line_ids': [Command.create({'name': 'Export', 'quantity': 1,
                                                                             'price_unit': 100.0, 'tax_ids': [Command.clear()]})]})
Line.create({'journal_id': eur_bank.id, 'date': D, 'payment_ref': 'EUR receipt Nile', 'partner_id': cust.id, 'amount': 100.0})

# early payment discount: 2/10 net 30, VAT 5% included in the discount; 1,050 invoice paid 1,029 within 10 days
term = env['account.payment.term'].create({
    'name': '2/10 net 30', 'early_discount': True, 'discount_percentage': 2.0, 'discount_days': 10,
    'early_pay_discount_computation': 'included',
    'line_ids': [Command.create({'value': 'percent', 'value_amount': 100.0, 'nb_days': 30})]})
vat = env['account.tax'].create({'name': 'VAT 5% (fixture)', 'amount': 5.0, 'type_tax_use': 'sale'})
inv_disc = post({'move_type': 'out_invoice', 'partner_id': cust.id, 'invoice_date': D, 'date': D,
                 'invoice_payment_term_id': term.id,
                 'invoice_line_ids': [Command.create({'name': 'Consulting', 'quantity': 1, 'price_unit': 1000.0,
                                                      'tax_ids': [Command.set(vat.ids)]})]})
Line.create({'journal_id': ahly.id, 'date': D + timedelta(days=2), 'payment_ref': 'Early payment Nile',
             'partner_id': cust.id, 'amount': 1029.0})

# internal transfer Misr -> Ahly
Line.create({'journal_id': misr.id, 'date': D, 'payment_ref': 'Transfer to Ahly', 'amount': -700.0})
Line.create({'journal_id': ahly.id, 'date': D, 'payment_ref': 'Transfer from Misr', 'amount': 700.0})
# bank fee
Line.create({'journal_id': ahly.id, 'date': D, 'payment_ref': 'Bank Fees October', 'amount': -12.5})

# batch deposit: two customer payments, one bank line
payments = env['account.payment']
for amount in (300.0, 450.0):
    pay = env['account.payment'].create({'payment_type': 'inbound', 'partner_type': 'customer', 'partner_id': cust.id,
                                         'amount': amount, 'journal_id': ahly.id, 'date': D})
    pay.action_post()
    payments |= pay
batch = env['ncollection.bank.payment.batch'].create({'journal_id': ahly.id, 'payment_type': 'inbound',
                                                      'payment_ids': [Command.set(payments.ids)]})
Line.create({'journal_id': ahly.id, 'date': D, 'payment_ref': 'Deposit slip 42', 'amount': 750.0})

# a duplicate booked twice by hand
for _i in range(2):
    Line.create({'journal_id': ahly.id, 'date': D, 'payment_ref': 'Cash deposit counter 3', 'amount': 95.0})
env.cr.commit()
print('GAPFIX eur invoice', inv_eur.name, inv_eur.line_ids.filtered(lambda ml: ml.account_id.account_type == 'asset_receivable').balance)
print('GAPFIX discount invoice', inv_disc.name, inv_disc.amount_total)
print('GAPFIX batch', batch.name, batch.amount, 'outstanding items', len(batch._nc_outstanding_items()))
print('GAPFIX ahly lines', Line.search_count([('journal_id', '=', ahly.id)]))
