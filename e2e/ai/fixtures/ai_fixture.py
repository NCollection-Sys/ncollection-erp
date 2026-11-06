# Seed for the AI chat widget browser acceptance. Run with `odoo shell` on the DISPOSABLE database only.
# Creates three users (accountant, Arabic accountant, plain internal user) plus an owner; passwords are throwaway
# test values that exist only in this disposable database.
GATE_OFF = 'ncollection_ai.enable_free_text_questions'
env['ir.config_parameter'].set_param(GATE_OFF, '')                       # free text OFF at first (#375 default)
env['ir.config_parameter'].set_param('ncollection_ai.gateway_url', 'http://ai-gateway:8080')

base_user = env.ref('base.group_user')
accountant = env.ref('ncollection_core.group_role_accountant')
owner = env.ref('ncollection_core.group_role_owner')


def make(login, password, groups, lang):
    user = env['res.users'].search([('login', '=', login)])
    if not user:
        user = env['res.users'].create({
            'name': login, 'login': login, 'password': password, 'lang': lang,
            'group_ids': [(6, 0, [g.id for g in groups])],
        })
    return user


make('ai_acct', 'Ai-Acct-Pass1!', [base_user, accountant], 'en_US')
make('ai_acct2', 'Ai-Acct2-Pass1!', [base_user, accountant], 'en_US')
make('ai_acct_ar', 'Ai-AcctAr-Pass1!', [base_user, accountant], 'ar_001')
make('ai_owner', 'Ai-Owner-Pass1!', [base_user, owner], 'en_US')
make('ai_plain', 'Ai-Plain-Pass1!', [base_user], 'en_US')
env['ir.module.module'].search([('name', 'like', 'ncollection_%')])._update_translations(['ar_001'])
env.registry.clear_all_caches()
env.cr.commit()
print('AIFIX done', env['res.users'].search_count([('login', 'like', 'ai\\_%')]))
