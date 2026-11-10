"""Seed a LOCAL, DEMONSTRATION-ONLY UAE e-invoicing workflow (run inside ``odoo shell -d <demo database>``).

What it does, idempotently (a second run changes nothing):

* names the e-invoicing module in the workspace licence mirror (``ncollection.workspace.config``) when the database has no
  tenant licence record, as a clearly labelled plan ``LOCAL-UAE-DEMO`` (a demonstration database has no platform to sync it from);
* gives the CURRENT company the demonstration identifiers the PINT-AE rules need (a fake TRN / TIN, an address, a legal
  registration) and enables the e-invoicing configuration with the tax mapping of the UAE chart;
* creates one demonstration buyer, one demonstration product and one posted customer invoice, and an e-invoice record for it.

It never submits anything: there is no provider configured and no call leaves the machine. The identifiers are TEST VALUES, not
real registrations: do not run it on a database that holds a real company's tax identity.
Usage: ``odoo shell -d <db> < scripts/upgrade/seed_uae_einvoice_demo.py``  (needs the UAE chart: taxes named "5% DU", "0%", "0% EXT").
"""
from odoo import Command, fields

company = env.company  # noqa: F821 - ``env`` is provided by ``odoo shell``
ae = env.ref('base.ae')  # noqa: F821
Config = env['ncollection.workspace.config'].sudo()  # noqa: F821
if not Config.search([], limit=1):
    names = env['ir.module.module'].sudo().search([('state', '=', 'installed')]).mapped('name')  # noqa: F821
    Config.create({'allowed_module_names': ','.join(names), 'plan_code': 'LOCAL-UAE-DEMO'})

state = env['res.country.state'].search([('country_id', '=', ae.id), ('code', '=', 'DU')], limit=1)  # noqa: F821
company._nc_apply_uae_localization()
if not company.partner_id.nc_trn:
    company.partner_id.write({
        'street': 'Demo Road', 'city': 'Dubai', 'state_id': state.id, 'country_id': ae.id,
        'nc_tin': '1000000001', 'nc_trn': '100000000000003', 'company_registry': '100000000000001',
        'nc_legal_reg_type': 'TL', 'nc_legal_reg_authority': 'Dubai Department of Economy (demo)'})

Tax = env['account.tax']  # noqa: F821


def sale_tax(name):
    return Tax.search([('company_id', '=', company.id), ('type_tax_use', '=', 'sale'), ('name', '=', name)], limit=1)


Map = env['ncollection.einvoice.tax.map']  # noqa: F821
for tax_name, category, extra in (('5% DU', 'S', {}), ('0%', 'Z', {}), ('0% EXT', 'E', {'exemption_code': 'DL8.46.1'})):
    tax = sale_tax(tax_name)
    if tax and not Map.search([('company_id', '=', company.id), ('tax_id', '=', tax.id)], limit=1):
        Map.create(dict({'company_id': company.id, 'tax_id': tax.id, 'pint_category': category}, **extra))
Config = env['ncollection.einvoice.config']  # noqa: F821
if not Config.search([('company_id', '=', company.id)], limit=1):
    Config.create({'company_id': company.id, 'enabled': True, 'default_payment_means_code': '10'})

abu_dhabi = env['res.country.state'].search([('country_id', '=', ae.id), ('code', '=', 'AZ')], limit=1)  # noqa: F821
buyer = env['res.partner'].search([('name', '=', 'DEMO Buyer LLC (e-invoicing)')], limit=1)  # noqa: F821
if not buyer:
    buyer = env['res.partner'].create({  # noqa: F821
        'name': 'DEMO Buyer LLC (e-invoicing)', 'company_type': 'company', 'street': 'Demo Street', 'city': 'Abu Dhabi',
        'state_id': abu_dhabi.id, 'country_id': ae.id, 'nc_tin': '1000000002', 'nc_trn': '100000000000103',
        'company_registry': '100000000000021', 'nc_legal_reg_type': 'TL', 'nc_legal_reg_authority': 'DED (demo)'})
product = env['product.product'].search([('default_code', '=', 'DEMO-EINV-1')], limit=1)  # noqa: F821
if not product:
    product = env['product.product'].create({  # noqa: F821
        'name': 'DEMO e-invoicing item', 'list_price': 100.0, 'nc_item_type': 'G', 'nc_hs_code': '88098432324',
        'default_code': 'DEMO-EINV-1'})
move = env['account.move'].search([('partner_id', '=', buyer.id), ('move_type', '=', 'out_invoice')], limit=1)  # noqa: F821
if not move:
    move = env['account.move'].create({  # noqa: F821
        'move_type': 'out_invoice', 'partner_id': buyer.id, 'invoice_date': fields.Date.today(),
        'invoice_date_due': fields.Date.add(fields.Date.today(), days=30),
        'invoice_line_ids': [Command.create({
            'product_id': product.id, 'quantity': 2, 'price_unit': 100.0, 'tax_ids': [Command.set(sale_tax('5% DU').ids)]})]})
    move.action_post()
doc = env['ncollection.einvoice'].search([('move_id', '=', move.id)], limit=1)  # noqa: F821
if not doc:
    move.action_nc_open_einvoice()
    doc = env['ncollection.einvoice'].search([('move_id', '=', move.id)], limit=1)  # noqa: F821
env.cr.commit()  # noqa: F821
print('SEED invoice', move.name, 'e-invoice', doc.id, 'local state', doc.local_state)  # noqa: T201
