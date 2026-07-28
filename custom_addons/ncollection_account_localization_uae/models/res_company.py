# -*- coding: utf-8 -*-
"""Per-company FTA readiness rollup + auto-seeding (F5-T01) + UAE VAT
localization application (P3-T04)."""
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# --- UAE currency & fixed peg rates (P3-T06) -------------------------------
# The UAE dirham is pegged to the US dollar at a fixed central-bank rate.
_AED_PER_USD = 3.6725
# Effective-from date for the seeded peg rates — early enough to cover any
# realistic invoice date (the AED/USD peg has held since 1997).
_UAE_PEG_RATE_DATE = '2020-01-01'
# USD value of one unit of each preloaded currency. The GCC pegs
# (SAR/QAR/OMR/BHD) are fixed central-bank pegs and exact; KWD (basket peg) and
# EUR (floating) are indicative starting points, refreshed later by the deferred
# automated-rate provider (see README). USD is the exact AED peg.
_UAE_CURRENCY_USD_PEG = {
    'USD': 1.0,
    'SAR': 1.0 / 3.75,       # SAMA peg: 3.75 SAR = 1 USD
    'QAR': 1.0 / 3.64,       # QCB peg: 3.64 QAR = 1 USD
    'OMR': 2.6008,           # CBO peg: 1 OMR = 2.6008 USD
    'BHD': 1.0 / 0.376,      # CBB peg: 0.376 BHD = 1 USD
    'KWD': 3.26,             # basket peg (indicative)
    'EUR': 1.08,             # floating (indicative)
}
# Currencies whose seeded rate is a non-authoritative snapshot (basket/floating)
# rather than a fixed peg — logged so provisioning shows they need refreshing.
_UAE_INDICATIVE_CURRENCIES = frozenset({'KWD', 'EUR'})


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
        chart template (P3-T04), enable bilingual UAE tax invoices (P3-T09) and
        the AED multi-currency setup (P3-T06).

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
                # AED default currency comes from the 'ae' chart; here we add
                # the rest of the UAE currency setup — activate the GCC
                # currencies, enable multi-currency, seed the fixed peg rates
                # and AED rounding (P3-T06). Self-fail-soft.
                company._nc_setup_uae_currencies()
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

    def _nc_setup_uae_currencies(self):
        """Set up UAE multi-currency for these companies (P3-T06).

        AED is already the company currency (loaded by the 'ae' chart, P3-T04)
        and Odoo core owns the currency/conversion engine — we only add the
        localization data on top:
          - activate AED + the preloaded currencies (USD, EUR + GCC);
          - enable the multi-currency UI group;
          - set AED rounding to the fils (0.01);
          - seed the fixed USD/AED peg rates (and GCC pegs) as
            ``res.currency.rate`` rows scoped to each company's MAIN (root)
            company — Odoo rejects rates on a branch company
            (``res.currency.rate._check_company_id``) and only reads the root
            company's rate at conversion time (``_get_rates`` filters on
            ``(False, root_id)``).

        The AED/USD peg is fixed (``_AED_PER_USD``), so a USD invoice converts to
        AED deterministically with no external rate feed. Automated freshness for
        floating currencies is a deferred follow-up (README / #236).

        Idempotent (skips a currency already rated for the root company) and
        fail-soft: every risky step runs in its OWN savepoint, so a single
        failure can neither poison the transaction nor starve the rest of the
        batch — the regression contract ``_nc_apply_uae_localization`` already
        keeps (a swallowed error without a savepoint aborts the whole cursor).
        """
        Currency = self.env['res.currency'].with_context(active_test=False)
        codes = ['AED'] + list(_UAE_CURRENCY_USD_PEG)
        currencies = Currency.search([('name', 'in', codes)])
        by_code = {c.name: c for c in currencies}
        Rate = self.env['res.currency.rate']
        rate_date = fields.Date.to_date(_UAE_PEG_RATE_DATE)

        # DB-global step (activate currencies, AED rounding, multi-currency
        # group), in its own savepoint so a failure leaves the cursor usable.
        try:
            with self.env.cr.savepoint():
                currencies.filtered(lambda c: not c.active).write(
                    {'active': True})
                aed = by_code.get('AED')
                if aed and aed.rounding != 0.01:
                    aed.rounding = 0.01
                self._nc_enable_multi_currency_group()
        except Exception:  # noqa: BLE001 - must never break install/provisioning
            _logger.warning(
                "UAE currency activation/rounding/multi-currency group failed; "
                "foreign-currency invoicing may need manual setup.", exc_info=True)

        # Per-company peg rates, scoped to the ROOT company, each in its own
        # savepoint so one bad row cannot poison the batch or abort a bulk
        # migration transaction.
        for company in self:
            root_id = company.root_id.id
            for code, usd_per_unit in _UAE_CURRENCY_USD_PEG.items():
                cur = by_code.get(code)
                if not cur or Rate.search_count([
                        ('currency_id', '=', cur.id),
                        ('company_id', '=', root_id)]):
                    continue
                try:
                    with self.env.cr.savepoint():
                        Rate.create({
                            'currency_id': cur.id,
                            'company_id': root_id,
                            'name': rate_date,
                            'rate': 1.0 / (usd_per_unit * _AED_PER_USD),
                        })
                    if code in _UAE_INDICATIVE_CURRENCIES:
                        _logger.info(
                            "Seeded INDICATIVE %s peg for company %s "
                            "(floating/basket — refresh via #236).",
                            code, root_id)
                except Exception:  # noqa: BLE001
                    _logger.warning(
                        "UAE peg-rate seed failed for company %s / %s; set it "
                        "manually.", root_id, code, exc_info=True)

    def _nc_enable_multi_currency_group(self):
        """Grant the multi-currency UI group so users can invoice in a foreign
        currency (the ORM already allows it; this is the Settings toggle's
        effect).

        Odoo core also auto-grants this when a second currency is activated
        (``res.currency._toggle_group_multi_currency``); we call core's own
        ``_apply_group`` explicitly so the group is guaranteed even when the
        currencies were already active (no ``active`` change to trigger core).
        ``_apply_group`` is itself idempotent.
        """
        group_mc = self.env.ref('base.group_multi_currency',
                                raise_if_not_found=False)
        group_user = self.env.ref('base.group_user', raise_if_not_found=False)
        if group_mc and group_user and group_mc not in group_user.all_implied_ids:
            group_user.sudo()._apply_group(group_mc.sudo())
            _logger.info(
                "Enabled multi-currency (group_multi_currency) for UAE tenant.")
