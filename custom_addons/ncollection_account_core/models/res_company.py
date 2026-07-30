# -*- coding: utf-8 -*-
"""Accounting engine baseline (F1-T02).

Codify the fiscal-year baseline on the company at provisioning time. Odoo owns
fiscal years / lock dates / journals (FINANCIAL_PLATFORM_ARCHITECTURE §4/§6) — we
only SET Odoo's own native ``res.company`` fields; we never add a fiscal-year
model or touch posting/tax. Journals come from the chart of accounts (Odoo /
l10n); lock dates stay operator-controlled (see README).
"""
import logging

from odoo import models

_logger = logging.getLogger(__name__)

# NCollection baseline fiscal year = the calendar year (31 December): the UAE
# standard and Odoo's own default, codified here so a freshly provisioned tenant
# is explicitly on it rather than relying on an unstated engine default.
_NC_FISCALYEAR_LAST_DAY = 31
_NC_FISCALYEAR_LAST_MONTH = '12'


class ResCompany(models.Model):
    _inherit = 'res.company'

    def _nc_apply_accounting_baseline(self):
        """Set each company's fiscal-year end to the NCollection baseline
        (31 Dec) on Odoo's own native fields.

        Idempotent (a no-op when already on the baseline) and fail-soft in its
        own savepoint, so it can never break module install / tenant
        provisioning. Runs on install (post_init_hook) against the fresh tenant
        company; it does not touch journals (owned by the chart of accounts) or
        lock dates (operator-controlled — auto-locking a fresh tenant's open
        periods would be wrong)."""
        for company in self:
            if (company.fiscalyear_last_day == _NC_FISCALYEAR_LAST_DAY
                    and company.fiscalyear_last_month == _NC_FISCALYEAR_LAST_MONTH):
                continue  # already on the baseline
            try:
                with self.env.cr.savepoint():
                    company.write({
                        'fiscalyear_last_day': _NC_FISCALYEAR_LAST_DAY,
                        'fiscalyear_last_month': _NC_FISCALYEAR_LAST_MONTH,
                    })
                _logger.info(
                    "Applied NCollection fiscal-year baseline (31 Dec) to "
                    "company %s (%s).", company.id, company.name)
            except Exception:  # noqa: BLE001 - must never break install/provisioning
                _logger.warning(
                    "Could not apply the fiscal-year baseline to company %s "
                    "(%s); configure it manually.", company.id, company.name,
                    exc_info=True)
