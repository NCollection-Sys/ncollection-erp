"""19.0.1.2.0: (1) Remove any audit rule this module seeded on a PLATFORM-LAYER model: OCA auditlog's hook on those
models bypassed the platform's own write logic (models/audit_rule.py, PLATFORM_MODULES). Their logged history is kept.
(2) Load this release's NEW terms in every active language (overwrite off)."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Rule = env['auditlog.rule'].sudo().with_context(active_test=False)
    platform = Rule.search([]).filtered(
        lambda rule: rule.model_id.model in env and env['auditlog.rule']._nc_platform_extended(env[rule.model_id.model])
        and rule.name.startswith('NCollection audit:'))
    if platform:
        if hasattr(platform, 'unsubscribe'):
            platform.unsubscribe()
        platform.unlink()
    langs = [code for code, _name in env['res.lang'].get_installed() if code != 'en_US']
    if langs:
        module = env['ir.module.module'].search([('name', '=', 'ncollection_audit')])
        module._update_translations(langs, overwrite=False)
