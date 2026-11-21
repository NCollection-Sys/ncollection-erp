# -*- coding: utf-8 -*-
# pylint: disable=print-used
# (stdout IS the transport back to the platform process — see seed_tenant.py.)
"""Assert a NEW tenant database really has every module it was provisioned with (feature parity gate).

Runs inside an `odoo shell` subprocess against the tenant database (never a cross-DB ORM call from the platform —
Rule 3), reading its expectation from the environment:

    NC_EXPECTED_MODULES  comma-separated technical names: the provisioning `-i` list

WHY THIS EXISTS. `odoo -i` exits 0 having NOT installed a module (an unknown name or `installable: False` is only a
WARNING), so "the init succeeded" is not evidence the tenant got its features. Before this gate a tenant could be
marked READY on the standard Odoo journal cards while the platform showed the enhanced ones. It checks, per module:

  * state is `installed` (not missing, `uninstalled`, `to install`, `to upgrade`);
  * its installed version is the version on disk (no pending upgrade);
  * every view the module ships is present and active (an inherited view deactivated or never loaded is exactly the
    "module installed, screen unchanged" case);
  * an `auto_install` module whose dependencies are ALL installed is installed too (Odoo's own rule — e.g.
    `ncollection_bank_reconciliation_sale` with `sale`).

Prints one `MODULES_OK=...` line on success, or raises. The caller treats a non-zero exit as a provisioning failure,
which rolls the database back and leaves the job retryable. Read-only.
"""
import os

from odoo.modules.module import get_manifest  # noqa: E402

expected = [m.strip() for m in (os.environ.get('NC_EXPECTED_MODULES') or '').split(',') if m.strip()]
if not expected:
    raise AssertionError('module check: NC_EXPECTED_MODULES is empty')

Module = env['ir.module.module'].sudo()  # noqa: F821
problems = []
installed = Module.search([('state', '=', 'installed')])
installed_names = set(installed.mapped('name'))

for name in expected:
    module = Module.search([('name', '=', name)], limit=1)
    if not module:
        problems.append('%s: not on the addons path' % name)
        continue
    if module.state != 'installed':
        problems.append('%s: state %s' % (name, module.state))
        continue
    # installed_version = the code's (adapted) manifest version; latest_version = what the database ran.
    if module.installed_version and module.latest_version != module.installed_version:
        problems.append('%s: installed %s, code %s' % (name, module.latest_version, module.installed_version))

pending = Module.search([('state', 'in', ('to install', 'to upgrade', 'to remove'))])
if pending:
    problems.append('pending operations: %s' % ', '.join(sorted('%s(%s)' % (m.name, m.state) for m in pending)))

# Odoo's auto_install rule, re-checked: a module whose dependencies are all installed must be installed itself.
# Only NCollection modules: Odoo installs an auto_install module only when one of its triggers is installed in the
# same run (ir_module.py must_install), so a generic Odoo module whose triggers were already there is legitimately
# absent; ours are what this gate is about.
for module in Module.search([('auto_install', '=', True), ('state', '=', 'uninstalled'),
                             ('name', '=like', r'ncollection\_%')]):
    manifest = get_manifest(module.name) or {}
    if not manifest.get('installable', True) or manifest.get('countries'):
        continue  # not installable, or country-gated (l10n_*): Odoo itself would not auto-install it here
    # `auto_install: [deps]` names the subset that triggers it (auto_install_required), not every dependency.
    deps = set(module.dependencies_id.filtered('auto_install_required').mapped('name'))
    if deps and deps <= installed_names:
        problems.append('%s: auto_install and every dependency installed, but not installed' % module.name)

# Every view an expected NCollection module ships must exist and be active (Odoo's own modules ship some views
# inactive by design, ours do not).
views = 0
for data in env['ir.model.data'].sudo().search([  # noqa: F821
        ('module', 'in', [m for m in expected if m.startswith('ncollection_')]), ('model', '=', 'ir.ui.view')]):
    view = env['ir.ui.view'].sudo().browse(data.res_id).exists()  # noqa: F821
    if not view:
        problems.append('view %s.%s: missing' % (data.module, data.name))
    elif not view.active:
        problems.append('view %s.%s: inactive' % (data.module, data.name))
    else:
        views += 1

if problems:
    raise AssertionError('module check FAILED: %s' % '; '.join(problems))

print('MODULES_OK=%d modules, %d views' % (len(expected), views))
