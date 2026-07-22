# -*- coding: utf-8 -*-
"""Tenant seed script (P2-T01/P2-T02) — executed by `odoo shell -d <tenant_db>`
in an ISOLATED subprocess from the provisioning engine (never a cross-DB ORM
call).

Reads tenant parameters from the environment and, inside the tenant DB:
  1. sets the company name (branding) and the workspace base URL,
  2. renames the admin user, sets an UNGUESSABLE password, and FORCES a reset —
     the tenant is "born hardened" (no known initial password; the owner sets
     their own on first login via the reset link),
  3. writes the ncollection.workspace.config projection (drives P1-T10 license
     enforcement),
  4. re-runs the cross-module role-implication sync so roles actually grant app
     access once the plan's modules are installed (closes regression R-014),
then commits and prints the reset URL for the welcome email. `env` is provided
by the odoo shell runtime.
"""
import os
import secrets

company_name = os.environ.get('NC_COMPANY', 'Tenant')
admin_email = (os.environ.get('NC_ADMIN_EMAIL') or '').strip()
allowed_modules = os.environ.get('NC_ALLOWED_MODULES', '')
plan_code = os.environ.get('NC_PLAN_CODE', '')
max_users = int(os.environ.get('NC_MAX_USERS') or '1')
sub_status = os.environ.get('NC_SUB_STATUS') or 'active'
portal_url = (os.environ.get('NC_PORTAL_URL') or '').strip()

# 1. Company / branding identity + workspace base URL (so reset/signup tokens
#    route to the tenant's own subdomain, not the platform host).
company = env.ref('base.main_company')  # noqa: F821 - `env` is the shell global
company.name = company_name
if portal_url:
    env['ir.config_parameter'].sudo().set_param('web.base.url', portal_url)  # noqa: F821

# 2. Admin user — rename, set login/email, assign an unguessable random password
#    then force a password reset. The owner never receives a credential; a reset
#    token is generated WITHOUT sending mail (no SMTP dependency at provisioning
#    time). The reset URL is printed below for the platform-side welcome email.
admin = env.ref('base.user_admin')  # noqa: F821
admin_vals = {
    'name': '%s Admin' % company_name,
    'password': secrets.token_urlsafe(32),  # unguessable; never disclosed
}
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

# 4. R-014: link cross-module role implications now that the plan's modules are
#    installed (core's post_init_hook ran during a single -i, before sale/account
#    groups existed, so it linked nothing). Idempotent.
from odoo.addons.ncollection_core.hooks import _sync_role_implications  # noqa: E402
_sync_role_implications(env)  # noqa: F821

env.cr.commit()  # noqa: F821

# Reset URL for the welcome email (valid in THIS tenant DB). auth_signup builds
# it from the reset token + web.base.url + signup_type.
setup_url = admin.partner_id._get_signup_url() or ''
print("SEED_SETUP_URL=%s" % setup_url)
print("SEED_OK")
