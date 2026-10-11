# -*- coding: utf-8 -*-
import logging

from odoo import models

_logger = logging.getLogger(__name__)


class ResGroups(models.Model):
    _inherit = 'res.groups'

    def _register_hook(self):
        """Keep the role -> app-group links in step with the installed apps.

        The roles imply groups that belong to OTHER modules (accounting, sales,
        ...). Those links can only be made once those modules exist, and the
        post_init hook / provisioning seed makes them once. A tenant provisioned
        before an app was installed (or by an older seed) therefore kept an
        Owner who could not use that app. Re-running on every registry load is
        the R-014 remedy: idempotent, and a no-op once the links exist.
        """
        result = super()._register_hook()
        try:
            from odoo.addons.ncollection_core.hooks import _sync_role_implications
            _sync_role_implications(self.env)
        except Exception:                                    # pragma: no cover
            # a registry that fails to load is an outage; a missing link is a gap
            _logger.exception("ncollection_core: syncing role implications failed")
        return result
