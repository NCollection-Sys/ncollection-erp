from datetime import timedelta

from odoo import Command, fields

assert env.cr.dbname == 'uxbrowse'
TODAY = fields.Date.today()
gulf = env['res.company'].search([('name', '=', 'Gulf Trading LLC')], limit=1)
refs = ['base.group_user', 'base.group_system', 'ncollection_core.group_role_ceo', 'ncollection_core.group_role_accountant',
        'ncollection_core.group_role_sales', 'ncollection_core.group_role_hr', 'ncollection_core.group_role_warehouse',
        'stock.group_stock_manager', 'hr.group_hr_user']
groups = [env.ref(x).id for x in refs if env.ref(x, raise_if_not_found=False)]
env['res.users'].create({
    'name': 'Dash Tester', 'login': 'dash_all', 'password': 'Dash-All-Pass1!', 'company_id': gulf.id,
    'company_ids': [Command.set(env['res.company'].search([]).ids)], 'lang': 'en_US', 'group_ids': [Command.set(groups)]})
# HR + warehouse rows so those dashboards show real records
dept = env['hr.department'].create({'name': 'Operations'})
for name in ('Amal Hassan', 'Omar Khalid', 'Layla Nasser'):
    env['hr.employee'].create({'name': name, 'department_id': dept.id, 'company_id': gulf.id})
env.cr.commit()
print('USERS ok')
