# Bank acceptance fixture for a REHEARSAL COPY of a real database (release rehearsal only; never the shared databases).
# Same data and users as bank_fixture.py, but in a NEW company ("Rehearsal Bank Co", AED, generic chart) so nothing that exists
# in the copy is touched: no currency change, no chart reload. The Odoo shell's database must be named on the command line
# (REHEARSAL_DB) so this cannot run by accident elsewhere.
import os
from datetime import timedelta

from odoo import Command, fields

assert env.cr.dbname in ('ncollection', 'awtar', 'ncollection_demo', 'uxbankweb'), 'rehearsal copies only'
assert os.environ.get('REHEARSAL_COPY') == '1', 'set REHEARSAL_COPY=1 (private rehearsal stack only)'
aed = env.ref('base.AED')
aed.active = True
company = env['res.company'].create({'name': 'Rehearsal Bank Co', 'currency_id': aed.id})
env.user.write({'company_ids': [Command.link(company.id)]})
env = env(context=dict(env.context, allowed_company_ids=[company.id]))
env['account.chart.template'].try_loading('generic_coa', company, install_demo=False)
env['res.lang']._activate_lang('ar_001')
env['res.lang']._activate_lang('en_US')
print('REHFIX company', company.id)
TODAY = fields.Date.today()
D = TODAY - timedelta(days=5)

print('BANKFIX journals', env['account.journal'].search([('company_id', '=', company.id)]).mapped(lambda j: (j.code, j.type)))
misr = env['account.journal'].search([('type', '=', 'bank'), ('company_id', '=', company.id)], limit=1)
if misr:
    misr.write({'code': 'BMSR'})
    for lang in ('en_US', 'ar_001'):
        misr.with_context(lang=lang).name = 'Bank Misr'
else:
    misr = env['account.journal'].create({'name': 'Bank Misr', 'type': 'bank', 'code': 'BMSR'})
ahly = env['account.journal'].create({'name': 'Ahly Bank', 'type': 'bank', 'code': 'BAHL'})
cash = env['account.journal'].search([('type', '=', 'cash'), ('company_id', '=', company.id)], limit=1) or     env['account.journal'].create({'name': 'Main Cash', 'type': 'cash', 'code': 'CSHM'})
cash.name = 'Main Cash'
for j in misr | ahly | cash:
    j.suspense_account_id = company.account_journal_suspense_account_id

# users (Option A: the Owner is not a technical administrator)
def user(login, name, roles):
    groups = [env.ref('base.group_user').id] + [env.ref(r).id for r in roles]
    return env['res.users'].create({'name': name, 'login': login, 'password': login.replace('_', '-').title() + '-Pass1!',
                                    'company_id': company.id, 'company_ids': [Command.set(company.ids)],
                                    'group_ids': [Command.set(groups)]})


owner = user('owner_bank', 'Owner Bank', ['ncollection_core.group_tenant_admin', 'ncollection_core.group_role_owner'])
acct = user('acct_bank', 'Accountant Bank', ['ncollection_core.group_role_accountant'])
ceo = user('ceo_bank', 'CEO Bank', ['ncollection_core.group_role_ceo'])
env['res.users'].browse(2).password = 'admin'

cust = env['res.partner'].create({'name': 'Nile Customer'})
cust2 = env['res.partner'].create({'name': 'شركة الدلتا'})
vend = env['res.partner'].create({'name': 'Delta Vendor'})


def invoice(partner, amount, move_type='out_invoice'):
    move = env['account.move'].create({'move_type': move_type, 'partner_id': partner.id, 'invoice_date': D,
                                       'invoice_line_ids': [Command.create({'name': 'Service', 'quantity': 1,
                                                                            'price_unit': amount, 'tax_ids': [Command.clear()]})]})
    move.action_post()
    return move


inv1, inv2, inv3 = invoice(cust, 1000.0), invoice(cust, 500.0), invoice(cust2, 2400.0)
bill = invoice(vend, 300.0, 'in_invoice')
Line = env['account.bank.statement.line']
# auto-matched by its label (= the invoice number): shows as reconciled
Line.create({'journal_id': misr.id, 'date': D, 'payment_ref': inv1.name, 'partner_id': cust.id, 'amount': 1000.0})
# to reconcile
Line.create({'journal_id': misr.id, 'date': D, 'payment_ref': 'Transfer from Nile', 'partner_id': cust.id, 'amount': 500.0})
Line.create({'journal_id': misr.id, 'date': D, 'payment_ref': 'شيك رقم 1042', 'amount': 2400.0})
Line.create({'journal_id': misr.id, 'date': D, 'payment_ref': 'Bank Fees March', 'amount': -25.0})
Line.create({'journal_id': misr.id, 'date': D, 'payment_ref': 'Payment Delta', 'partner_id': vend.id, 'amount': -300.0})
Line.create({'journal_id': cash.id, 'date': D, 'payment_ref': 'Cash sale', 'amount': 150.0})
env.cr.commit()
for u in (owner, acct, ceo):
    print('BANKFIX', u.login, 'write' if u.has_group('account.group_account_user') else 'read',
          'system' if u.has_group('base.group_system') else '-')
print('BANKFIX invoices', inv1.name, inv1.amount_residual, inv2.name, inv3.name)
print('BANKFIX misr lines', Line.search_count([('journal_id', '=', misr.id)]),
      'reconciled', Line.search_count([('journal_id', '=', misr.id), ('is_reconciled', '=', True)]))
