from datetime import timedelta

from odoo import Command, fields

assert env.cr.dbname == 'uxbrowse'
TODAY = fields.Date.today()
aed, eur = env.ref('base.AED'), env.ref('base.EUR')
aed.active = True
eur.active = True


def company(name, currency):
    co = env['res.company'].create({'name': name})
    env['account.chart.template'].with_company(co).try_loading('generic_coa', co, install_demo=False)
    co.currency_id = currency
    if not env['stock.warehouse'].search([('company_id', '=', co.id)]):
        env['stock.warehouse'].with_company(co).create({'name': co.name[:20], 'code': co.name[:3].upper(), 'company_id': co.id})
    return co


gulf = company('Gulf Trading LLC', aed)
empty = company('Empty AED Co', aed)
euro = company('Euro Works GmbH', eur)
env['res.currency.rate'].create({'name': TODAY - timedelta(days=4000), 'currency_id': eur.id, 'rate': 0.25, 'company_id': gulf.id})
admin = env['res.users'].browse(2)
admin.write({'company_ids': [Command.link(gulf.id), Command.link(empty.id), Command.link(euro.id)]})
admin.group_ids = [Command.link(env.ref('ncollection_core.group_role_owner').id),
                   Command.link(env.ref('ncollection_core.group_role_accountant').id)]
names = ['Al Noor Trading', 'Desert Rose Foods', 'Marina Tech Supplies', 'Falcon Logistics']
partners = [env['res.partner'].create({'name': n}) for n in names]
product_a = env['product.product'].create({'name': 'Office Chair Pro', 'type': 'consu', 'list_price': 100})
product_b = env['product.product'].create({'name': 'Standing Desk', 'type': 'consu', 'list_price': 100})
salesman = env['res.users'].create({'name': 'Sara Salesperson', 'login': 'sara_prem', 'group_ids': [
    Command.set([env.ref('base.group_user').id, env.ref('sales_team.group_sale_salesman').id])]})


def order(co, partner, product, price, qty, currency, confirm=True, days_ago=2):
    pricelist = env['product.pricelist'].create({'name': 'PL %s %s' % (currency.name, co.id), 'currency_id': currency.id, 'company_id': False})
    so = env['sale.order'].with_company(co).create({
        'partner_id': partner.id, 'company_id': co.id, 'pricelist_id': pricelist.id, 'user_id': salesman.id,
        'order_line': [Command.create({'product_id': product.id, 'product_uom_qty': qty, 'price_unit': price, 'tax_ids': [Command.clear()]})]})
    if confirm:
        so.action_confirm()
    so.date_order = fields.Datetime.now() - timedelta(days=days_ago)
    return so


order(gulf, partners[0], product_a, 4200.0, 3, aed)
order(gulf, partners[1], product_b, 7800.0, 2, aed, days_ago=4)
order(gulf, partners[2], product_a, 1500.0, 4, eur, days_ago=6)          # 6,000 EUR = 24,000 AED
order(gulf, partners[3], product_b, 3300.0, 1, aed, confirm=False)
order(gulf, partners[0], product_a, 900.0, 2, aed, confirm=False, days_ago=1)
order(euro, partners[1], product_a, 800.0, 5, eur)                          # another company, another currency


def invoice(co, partner, amount, move_type='out_invoice', days_ago=3, due_days=30):
    journal = env['account.journal'].search([('company_id', '=', co.id), ('type', '=', 'sale')], limit=1)
    m = env['account.move'].with_company(co).create({
        'move_type': move_type, 'partner_id': partner.id, 'company_id': co.id, 'journal_id': journal.id,
        'invoice_date': TODAY - timedelta(days=days_ago), 'invoice_date_due': TODAY - timedelta(days=days_ago) + timedelta(days=due_days),
        'invoice_payment_term_id': False,
        'invoice_line_ids': [Command.create({'name': 'Goods', 'quantity': 1, 'price_unit': amount, 'tax_ids': [Command.clear()]})]})
    m.action_post()
    return m


i1 = invoice(gulf, partners[0], 12600.0, days_ago=3)
i2 = invoice(gulf, partners[1], 15600.0, days_ago=5)
i3 = invoice(gulf, partners[2], 24000.0, days_ago=7)
old = invoice(gulf, partners[3], 8000.0, days_ago=70, due_days=30)          # overdue by 40 days
invoice(gulf, partners[0], 1500.0, 'out_refund', days_ago=2)
invoice(euro, partners[1], 4000.0, days_ago=2)
reg = env['account.payment.register'].with_company(gulf).with_context(active_model='account.move', active_ids=i1.ids)
reg.create({'amount': 12600.0})._create_payments()
env['account.payment.register'].with_company(gulf).with_context(active_model='account.move', active_ids=i2.ids).create({'amount': 6000.0})._create_payments()
env.cr.commit()
print('PREMIUM', gulf.id, empty.id, euro.id)
