# -*- coding: utf-8 -*-
"""Let the corrected welcome email replace the stale one on existing databases.

The welcome template used to sit inside a ``noupdate="1"`` block, and an
``ir.model.data`` row remembers that flag: moving the record into a
``noupdate="0"`` block in the data file does NOT flip it, so an upgrade would
still skip the record and every existing platform would keep sending the old
text ("set your password", no username). Clearing the flag HERE, before the data
files load, is what lets the upgrade rewrite it.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute(
        """UPDATE ir_model_data SET noupdate = FALSE
            WHERE module = 'ncollection_saas' AND name = 'mail_template_tenant_welcome'
              AND model = 'mail.template' AND noupdate""")
    _logger.info("ncollection_saas: welcome template unlocked for refresh (%s row)",
                 cr.rowcount)
