# -*- coding: utf-8 -*-
"""Per-company FTA readiness rollup + auto-seeding (F5-T01) + UAE VAT
localization application (P3-T04)."""
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class ResCompany(models.Model):
    _inherit = 'res.company'

    fta_item_ids = fields.One2many(
        'ncollection.fta.compliance.item', 'company_id',
        string='FTA Compliance Items')
    fta_readiness = fields.Integer(
        string='FTA Readiness %', compute='_compute_fta_readiness',
        help='Percentage of applicable UAE FTA compliance items marked done '
             '(items marked "not applicable" are excluded from the base).')

    @api.depends('fta_item_ids.state')
    def _compute_fta_readiness(self):
        for company in self:
            applicable = company.fta_item_ids.filtered(
                lambda i: i.state != 'not_applicable')
            done = applicable.filtered(lambda i: i.state == 'done')
            company.fta_readiness = (
                round(100 * len(done) / len(applicable)) if applicable else 0)

    @api.model_create_multi
    def create(self, vals_list):
        companies = super().create(vals_list)
        # Seed each new company's FTA checklist (sudo: company creation may run
        # in a context without account-manager create rights on the item model).
        Item = self.env['ncollection.fta.compliance.item'].sudo()
        for company in companies:
            Item._ensure_for_company(company)
        return companies

    def _nc_apply_uae_localization(self):
        """Set up UAE localization on each company: load Odoo's official 'ae'
        chart template (P3-T04) and enable bilingual UAE tax invoices (P3-T09).

        The 'ae' template (l10n_ae) OWNS the accounting mechanism — chart of
        accounts, 5% standard / 0% zero-rated / exempt taxes, tax groups and the
        domestic/GCC/international fiscal positions — and wires the company's
        default sale/purchase taxes to it. We only trigger the load; we don't
        redefine any of it (FPA §7: mechanisms stay Odoo-owned).

        For invoices, we flip on l10n_gcc's dual-language layout so the tax
        invoice renders bilingual Arabic/English (the layout itself is
        Odoo-owned, l10n_ae/l10n_gcc) — but only on a company we actually
        localize here, mirroring the chart guard below.

        Idempotent + fail-soft, so it is safe to call from post_init /
        provisioning:
          - skips a company that already has a chart of accounts loaded
            (``chart_template`` set) — never clobbers existing accounting;
          - never raises: a localization failure must not break module install
            or tenant provisioning (house style, like the mail/seed hooks).
        """
        ChartTemplate = self.env['account.chart.template']
        for company in self:
            # Skip a company already on a REAL localization. 'generic_coa' is
            # Odoo's placeholder fallback (see below) — treat it as "not
            # localized" so a company that got the generic chart is still
            # upgraded to the UAE one. (Assumption: this module is UAE-specific,
            # so any company in a tenant where it is installed is a UAE company —
            # db-per-tenant means one company; try_loading also sets country=AE.)
            if company.chart_template and company.chart_template != 'generic_coa':
                _logger.info(
                    "Company %s (%s) already on chart '%s' — leaving it as is; "
                    "UAE VAT not forced.",
                    company.id, company.name, company.chart_template)
                continue
            try:
                with self.env.cr.savepoint():
                    ChartTemplate.try_loading('ae', company, install_demo=False)
                # Odoo core's `account` install schedules a DEFERRED
                # try_loading('generic_coa') on registry._auto_install_template,
                # consumed in ir.module _register_hook AFTER every post_init hook
                # has run — it would otherwise UNLINK the 'ae' chart we just
                # loaded and replace it with the generic 15% placeholder. Cancel
                # that pending fallback now that the correct chart is in place.
                # (registry-GLOBAL: Odoo only keeps the single last-scheduled
                # closure, for the one company it targeted — safe to cancel under
                # db-per-tenant / one company per DB.)
                if hasattr(self.env.registry, '_auto_install_template'):
                    del self.env.registry._auto_install_template
                # Now that this company is a UAE (l10n_ae) company, turn on the
                # bilingual tax-invoice layout for it (P3-T09). Gated to a
                # company we actually localized, mirroring the skip above.
                company._nc_enable_bilingual_invoices()
            except Exception:  # noqa: BLE001 - must never break install/provisioning
                # savepoint rolled the partial load back, so the company stays on
                # no/placeholder chart and remains RETRYABLE (the generic fallback,
                # if still pending, provides basic accounting — some > none).
                _logger.error(
                    "Could not apply the UAE 'ae' chart template to company %s "
                    "(%s) — UAE VAT is NOT set up; rolled back, retryable.",
                    company.id, company.name, exc_info=True)

    def _nc_enable_bilingual_invoices(self):
        """Turn on the bilingual Arabic/English tax-invoice layout for these
        companies (P3-T09).

        The layout itself is Odoo-owned (``l10n_ae`` → ``l10n_gcc_invoice``); it
        renders the Arabic column only when BOTH are true:
          - the company's ``l10n_gcc_dual_language_invoice`` flag is set, and
          - Arabic (``ar_001``) is an ACTIVE language.
        We only flip the per-company flag here: ``l10n_gcc_invoice`` (a hard
        transitive dependency, installed before us) already activates Arabic
        globally in its own ``post_init`` hook
        (``_activate_and_install_lang('ar_001')``), so the language condition is
        met by the time this runs. Broader Arabic/RTL UI enablement is P3-T08.

        Idempotent: only writes companies that don't already have the flag.
        """
        # Defense-in-depth: the field comes from l10n_gcc_invoice (a hard dep, so
        # always present when this module is installed); the guard just keeps this
        # inert should that dependency chain ever be relaxed.
        if 'l10n_gcc_dual_language_invoice' in self._fields:
            self.filtered(
                lambda c: not c.l10n_gcc_dual_language_invoice
            ).l10n_gcc_dual_language_invoice = True
