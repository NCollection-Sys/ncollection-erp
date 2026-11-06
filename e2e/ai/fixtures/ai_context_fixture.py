
# ---- five-context records (appended to ai_fixture.py by run_ai_web.sh when AI_SEED is set) ----
partner = env['res.partner'].create({'name': 'AI Context Customer'})
so = env['sale.order'].create({'partner_id': partner.id})
lead = env['crm.lead'].create({'name': 'AI Context Lead', 'partner_id': partner.id})
company = env.company
journal = env['account.journal'].search([('type', '=', 'sale'), ('company_id', '=', company.id)], limit=1) or \
    env['account.journal'].create({'name': 'AI Sales', 'code': 'AIS', 'type': 'sale', 'company_id': company.id})
move = env['account.move'].create({'move_type': 'out_invoice', 'partner_id': partner.id, 'journal_id': journal.id})
ptype = env['stock.picking.type'].search([('code', '=', 'outgoing')], limit=1)
picking = env['stock.picking'].create({'picking_type_id': ptype.id, 'partner_id': partner.id})
env.cr.commit()
print('AIFIX ids', {'sale.order': so.id, 'crm.lead': lead.id, 'account.move': move.id, 'stock.picking': picking.id})
# Widget chips are served in the viewer's language; an inactive en_US would silently make English users fall back to Arabic.
env['res.lang']._activate_lang('en_US')
env['ir.module.module'].search([('name', 'like', 'ncollection_%')])._update_translations(['ar_001'])
env.registry.clear_all_caches()
env.cr.commit()
print('AIFIX langs', env['res.lang'].search([]).mapped('code'))
