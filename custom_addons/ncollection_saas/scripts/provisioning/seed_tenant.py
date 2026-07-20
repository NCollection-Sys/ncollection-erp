# -*- coding: utf-8 -*-
"""Tenant seed script (P2-T01) — executed by `odoo shell -d <tenant_db>` in an
ISOLATED subprocess from the provisioning engine (never a cross-DB ORM call).

Reads tenant parameters from the environment and, inside the tenant DB:
  1. sets the company name (branding),
  2. renames the admin user + FORCES a password reset (no known password —
     secure by default, ARCHITECTURE_SECURITY §4/§6),
  3. writes the ncollection.workspace.config projection (drives P1-T10 license
     enforcement),
then commits. `env` is provided by the odoo shell runtime.
"""
import os

company_name = os.environ.get('NC_COMPANY', 'Tenant')
admin_email = (os.environ.get('NC_ADMIN_EMAIL') or '').strip()
allowed_modules = os.environ.get('NC_ALLOWED_MODULES', '')
plan_code = os.environ.get('NC_PLAN_CODE', '')
max_users = int(os.environ.get('NC_MAX_USERS') or '1')
sub_status = os.environ.get('NC_SUB_STATUS') or 'active'

# 1. Company / branding identity.
company = env.ref('base.main_company')  # noqa: F821 - `env` is the shell global
company.name = company_name

# 2. Admin user — rename, set the login/email, force a password reset. A reset
#    token is generated WITHOUT sending mail (no SMTP dependency at provisioning
#    time); the owner sets their own password on first login.
admin = env.ref('base.user_admin')  # noqa: F821
admin_vals = {'name': '%s Admin' % company_name}
if admin_email:
    admin_vals['login'] = admin_email
    admin_vals['email'] = admin_email
admin.write(admin_vals)
admin.partner_id.signup_prepare(signup_type='reset')

# 3. Workspace config projection (the tenant-side copy of what the plan allows).
Config = env['ncollection.workspace.config']  # noqa: F821
cfg_vals = {
    'allowed_module_names': allowed_modules,
    'plan_code': plan_code,
    'subscription_status': sub_status,
    'max_users': max_users,
}
existing = Config.search([], limit=1)
if existing:
    existing.write(cfg_vals)
else:
    Config.create(cfg_vals)

env.cr.commit()  # noqa: F821
print("SEED_OK")
