# -*- coding: utf-8 -*-
"""Deliberate localization of an EXISTING tenant (#469).

Provisioning localizes a tenant at creation, which is the only moment loading a
chart of accounts is safe. This is the other case: a database that already
exists and is on the wrong chart.

IT IS A BUTTON, NOT A MIGRATION, and that is the whole design. There is no
cron, no fleet sweep and no upgrade hook that calls it — deliberately. Changing
a live tenant's chart of accounts and currency is a business decision with tax
consequences, and Odoo's own loader will either delete their books or stack a
second chart on top of the first. Neither outcome may be reached by a scheduled
job.

The safety properties, in order of how badly they fail without them:

  * a database holding accounting entries is REFUSED (in the tenant-side
    script, where the count is truthful) unless a human passes force;
  * a company already on the target chart is a successful no-op, so a retry
    after a subprocess timeout cannot double-load;
  * the whole thing runs in an isolated `odoo shell` subprocess against the
    tenant database — never a cross-DB ORM call (Rule 3);
  * the result is VERIFIED afterwards, because the country module's own hook is
    fail-soft and a silent skip looks identical to success.
"""
import logging
import os

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

APPLY_LOCALIZATION_SCRIPT = os.path.join(
    os.path.dirname(__file__), '..', 'scripts', 'provisioning',
    'apply_localization.py'
)


class TenantLocalization(models.Model):
    _name = 'ncollection.tenant'
    _inherit = ['ncollection.tenant', 'ncollection.saas.subprocess.mixin']

    localization_applied_state = fields.Selection(
        [('none', 'Not applied'), ('done', 'Applied'), ('refused', 'Refused'),
         ('error', 'Failed')],
        string='Localization Applied', default='none', readonly=True, copy=False)
    localization_applied_message = fields.Text(
        string='Localization Result', readonly=True, copy=False)

    def action_apply_localization(self):
        """Apply this tenant's country localization to its existing database.

        Synchronous and one tenant at a time on purpose: an operator pressing
        this is making a decision about THIS tenant and needs the answer, not a
        queued job whose refusal they will never read. It is also why this does
        not go through queue_job — there is no fleet operation here to batch.
        """
        result = None
        for tenant in self:
            result = tenant._nc_apply_localization_one()
        return result

    def _nc_apply_localization_one(self, force=False):
        self.ensure_one()
        package = self._nc_localization_package()
        if not package:
            raise UserError(self.env._(
                "Tenant '%s' has no country localization package. Set a "
                "supported country first.", self.company_name))
        if self.database_status != 'ready' or not self.database_name:
            raise UserError(self.env._(
                "Tenant '%s' has no ready database to localize.",
                self.company_name))
        # Same guard provisioning uses before it touches any database name.
        self._assert_safe_db_name(self.database_name)

        env_vars = os.environ.copy()
        env_vars.update({
            'NC_LOC_CHART': package['chart_template'],
            'NC_LOC_CURRENCY': package['currency'],
            'NC_LOC_FORCE': '1' if force else '',
        })
        with open(APPLY_LOCALIZATION_SCRIPT, encoding='utf-8') as fh:
            script = fh.read()
        cmd = (['odoo', 'shell']
               + self._odoo_conn_args(self.database_name)
               + ['--log-level=error'])
        try:
            out = self._run_odoo_subprocess(
                cmd, self.env._("apply localization"), stdin=script,
                env=env_vars)
        except Exception as exc:  # noqa: BLE001 — the refusal IS the result
            message = str(exc)
            # A refusal is not a fault: it is the guard doing its job, and it
            # must read as a decision the operator has to make rather than as a
            # broken button.
            refused = 'REFUSED:' in message
            # RECORDED, NOT RAISED. Raising would roll this write back with the
            # transaction and leave the operator with a dialog and a record
            # that says nothing happened — so the one place they would look
            # afterwards would be empty. The outcome belongs on the tenant.
            self.sudo().write({
                'localization_applied_state': 'refused' if refused else 'error',
                'localization_applied_message': message[-4000:],
            })
            _logger.warning("Localization of tenant %s %s: %s",
                            self.database_name,
                            'refused' if refused else 'failed', message)
            return self._nc_localization_notification(
                title=(self.env._("Localization refused") if refused
                       else self.env._("Localization failed")),
                message=message[-800:],
                sticky=True,
                warning=True,
            )

        detail = next((line for line in (out or '').splitlines()
                       if line.startswith('LOCALIZATION_APPLIED=')), '')
        self.sudo().write({
            'localization_applied_state': 'done',
            'localization_applied_message':
                detail[len('LOCALIZATION_APPLIED='):] or 'ok',
        })
        _logger.info("Localization applied to tenant %s: %s",
                     self.database_name, detail)
        # Licensing follows the localization: the package's modules are part of
        # the tenant's effective set, so push it through the EXISTING sync
        # rather than writing the tenant's config from here.
        self._config_sync_enqueue()
        return self._nc_localization_notification(
            title=self.env._("Localization applied"),
            message=self.localization_applied_message,
        )

    def _nc_localization_notification(self, title, message, sticky=False,
                                      warning=False):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title,
                'message': message,
                'sticky': sticky,
                'type': 'warning' if warning else 'success',
            },
        }

    @api.model
    def _nc_localization_is_never_automatic(self):
        """Executable documentation, asserted by the tests.

        Nothing in this module may call `action_apply_localization` from a cron,
        a migration or a plan write. If that ever changes, the test that reads
        this contract fails and the reviewer is told why it exists.
        """
        return True
