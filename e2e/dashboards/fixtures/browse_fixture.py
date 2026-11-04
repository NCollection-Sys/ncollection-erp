import base64
import io
from datetime import timedelta

from odoo import Command, fields

assert env.cr.dbname == 'uxbrowse'
company = env.company
env['account.chart.template'].try_loading('generic_coa', company, install_demo=False)
TODAY = fields.Date.today()
eur = env.ref('base.EUR')
eur.active = True
env['res.currency.rate'].create({'name': TODAY - timedelta(days=400), 'currency_id': eur.id, 'rate': 2.0,
                                 'company_id': company.id})
env['res.lang']._activate_lang('ar_001')

# users: the Tenant Owner (no administrator rights) and a plain employee
owner_groups = [env.ref('base.group_user').id, env.ref('ncollection_core.group_tenant_admin').id,
                env.ref('ncollection_core.group_role_owner').id]
for xml in ('account.group_account_manager', 'sales_team.group_sale_manager', 'purchase.group_purchase_manager',
            'product.group_product_manager', 'base.group_partner_manager'):
    ref = env.ref(xml, raise_if_not_found=False)
    if ref:
        owner_groups.append(ref.id)
owner = env['res.users'].create({'name': 'Owner Ux', 'login': 'owner_ux', 'password': 'Ux-Owner-Pass1!',
                                 'group_ids': [Command.set(owner_groups)]})
env['res.users'].browse(2).password = 'admin'

# product with an image
from PIL import Image  # noqa: E402
buf = io.BytesIO()
Image.new('RGB', (1600, 1200), (30, 120, 200)).save(buf, 'PNG')
env['product.template'].create({'name': 'Ux Product With Image', 'list_price': 25.0, 'default_code': 'UX-IMG',
                                'image_1920': base64.b64encode(buf.getvalue())})
env['product.template'].create({'name': 'Ux Product Without Image', 'list_price': 10.0})

cust = env['res.partner'].create({'name': 'Ux Customer', 'vat': 'UX123'})
cust_ar = env['res.partner'].create({'name': 'عميل تجريبي'})


def doc(amount, partner=cust, due=30, move_type='out_invoice', taxed=False):
    tax = env['account.tax'].search([('type_tax_use', '=', 'sale'), ('amount', '>', 0)], limit=1)
    move = env['account.move'].create({
        'move_type': move_type, 'partner_id': partner.id, 'invoice_date': TODAY - timedelta(days=40),
        'invoice_date_due': TODAY + timedelta(days=due), 'invoice_payment_term_id': False,
        'invoice_line_ids': [Command.create({'name': 'Ux line', 'quantity': 1, 'price_unit': amount,
                                             'tax_ids': [Command.set(tax.ids)] if taxed else [Command.clear()]})]})
    move.action_post()
    return move


def pay(move, amount):
    reg = env['account.payment.register'].with_context(active_model='account.move', active_ids=move.ids).create(
        {'amount': amount})
    return reg._create_payments()


unpaid = doc(500.0)
partial = doc(600.0)
pay(partial, 250.0)
paid = doc(300.0)
pay(paid, 300.0)
over = doc(800.0, cust_ar, taxed=True)
pay(over, 1200.0)
overdue = doc(400.0, due=-10)
doc(90.0, taxed=True)
env.cr.commit()
print('FIXTURE-OK', unpaid.name, over.name, over.nc_overpaid_amount)
