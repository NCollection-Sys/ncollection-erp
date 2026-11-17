# -*- coding: utf-8 -*-
"""XBRL 2.1 instance export for the financial statements that map onto the IFRS Accounting Taxonomy 2025 (Full IFRS).

Owner decision 2026-11-16: initial profile IFRS Accounting Taxonomy 2025, validated independently with Arelle.

WHAT IS EXPORTED. Exactly the figures the report itself computes (``_nc_bucket_totals`` / ``_nc_pl_figures``, the
same code path as the screen, PDF and Excel), for the report's period and, when the report has a comparison period,
for that period too. A report line is exported ONLY under an official concept that means the same thing:

  Balance Sheet (instant)  CurrentAssets, NoncurrentAssets, Assets, CurrentLiabilities, NoncurrentLiabilities,
                           Liabilities, Equity (equity accounts + accumulated earnings, as the report shows it),
                           EquityAndLiabilities
  Profit and Loss (period) Revenue, OtherIncome, CostOfSales, ProfitLoss

NOT exported, on purpose: the report's "Gross Profit" includes other income (IFRS GrossProfit is revenue minus cost of
sales), and its "Operating Expenses" bucket has no single equivalent concept in the full taxonomy. Exporting them
under a near concept would be a false statement in a machine-readable file.

  Changes in Equity        the TOTAL EQUITY column only, with no ComponentsOfEquityAxis member: Equity at the opening
                           (day before the period) and closing instants, ProfitLoss (the movement of the accumulated
                           result: the period's income and expense) and ChangesInEquity (the total movement)

NOT exported for Changes in Equity, on purpose: the per-component columns. The report's components are Odoo account
TYPES ("equity" holds share capital AND reserves AND, on many charts, retained earnings; "equity_unaffected" is the
current-year earnings account), while the IFRS members are economic components (IssuedCapitalMember,
RetainedEarningsMember, OtherReservesMember, ...). No account-to-member mapping is kept in the books, so any member
would be a guess. Nor the split of the non-profit movement (dividends, share issues, transfers): the ledger does not
classify those movements either. See docs/markdown/XBRL_EXPORT.md.

WHAT THIS IS NOT. A filing. The file says nothing about whether the company's statements comply with IFRS, and it has
not been tested against any regulator's filing rules (docs/markdown/XBRL_EXPORT.md). No accounting record is read
with elevated rights and nothing is written.
"""
import re

from dateutil.relativedelta import relativedelta
from lxml import etree

from odoo import api, models
from odoo.exceptions import UserError

TAXONOMY_ENTRY_POINT = 'https://xbrl.ifrs.org/taxonomy/2025-03-27/full_ifrs_entry_point_2025-03-27.xsd'
NS = {
    'xbrli': 'http://www.xbrl.org/2003/instance',
    'link': 'http://www.xbrl.org/2003/linkbase',
    'xlink': 'http://www.w3.org/1999/xlink',
    'iso4217': 'http://www.xbrl.org/2003/iso4217',
    'ifrs-full': 'https://xbrl.ifrs.org/taxonomy/2025-03-27/ifrs-full',
}
ENTITY_SCHEME = 'https://www.ncollection.com/xbrl/entity'

BALANCE_SHEET_MAP = (
    ('CurrentAssets', ('current_assets',)),
    ('NoncurrentAssets', ('fixed_assets',)),
    ('Assets', ('current_assets', 'fixed_assets')),
    ('CurrentLiabilities', ('current_liabilities',)),
    ('NoncurrentLiabilities', ('long_term_liabilities',)),
    ('Liabilities', ('current_liabilities', 'long_term_liabilities')),
    ('Equity', ('equity', 'accumulated_earnings')),
    ('EquityAndLiabilities', ('current_liabilities', 'long_term_liabilities', 'equity', 'accumulated_earnings')),
)
# Indirect-method Cash Flow (wizard/cash_flow.py ``_nc_cash_flow_figures``). Not exported: "Movement in working
# capital" (no single concept). The net change is the figure BEFORE exchange-rate effects, which the report does not
# compute separately, so the matching concept is the "before effect" one.
CASH_FLOW_DURATION = (
    ('ProfitLoss', 'net_profit'),
    ('AdjustmentsForDepreciationExpense', 'depreciation'),
    ('CashFlowsFromUsedInOperatingActivities', 'operating'),
    ('CashFlowsFromUsedInInvestingActivities', 'investing'),
    ('CashFlowsFromUsedInFinancingActivities', 'financing'),
    ('IncreaseDecreaseInCashAndCashEquivalentsBeforeEffectOfExchangeRateChanges', 'net_change'),
)
PROFIT_LOSS_MAP = (
    ('Revenue', 'revenue'),
    ('OtherIncome', 'other_income'),
    ('CostOfSales', 'cogs'),
    ('ProfitLoss', 'net_profit'),
)


class AccountReportXbrl(models.AbstractModel):
    _name = 'ncollection.account.report.xbrl'
    _description = 'XBRL export (IFRS Accounting Taxonomy 2025)'

    @api.model
    def _nc_supported_models(self):
        return {'ncollection.account.report.balance.sheet': 'balance_sheet',
                'ncollection.account.report.profit.loss': 'profit_loss',
                'ncollection.account.report.cash.flow': 'cash_flow',
                'ncollection.account.report.equity.changes': 'equity_changes'}

    @api.model
    def _nc_unavailable_reason(self, model):
        """Why ``model`` has no XBRL export (shown in the Export menu), or False when it has one. Never a fake file:
        a report that is not an IFRS statement says so (docs/markdown/XBRL_EXPORT.md, the report-by-report matrix)."""
        if model in self._nc_supported_models():
            return False
        ledgers = self.env._("An account-level listing: IFRS has no concept per account row, so a mapping would be "
                             "invented.")
        reasons = {
            'ncollection.account.report.trial.balance': ledgers,
            'ncollection.account.report.general.ledger': ledgers,
            'ncollection.account.report.partner.ledger': ledgers,
            'ncollection.account.report.statement': ledgers,
            'ncollection.account.report.aged.partner': ledgers,
            'ncollection.account.report.tax.analysis': self.env._(
                "A tax return analysis, not an IFRS statement; a tax authority taxonomy would be needed."),
            'ncollection.account.report.uae.vat': self.env._(
                "The UAE VAT return is filed with the FTA, which publishes no XBRL taxonomy for it."),
        }
        return reasons.get(model) or self.env._(
            "A management analysis, not an IFRS financial statement: no taxonomy concept means the same thing.")

    @api.model
    def _nc_facts(self, wizard):
        """[(concept, period_type, start, end, value)] from the report's own figures, current then comparison."""
        kind = self._nc_supported_models().get(wizard._name)
        if not kind:
            raise UserError(self.env._(
                "XBRL is available for the Balance Sheet, the Profit and Loss, the Cash Flow and the "
                "Statement of Changes in Equity only."))
        records = [wizard]
        comparison = wizard._nc_comparison_record() if hasattr(wizard, '_nc_comparison_record') else None
        if comparison is not None:
            records.append(comparison)
        facts = []
        for record in records:
            if kind == 'balance_sheet':
                totals = wizard._nc_bucket_totals(record)[0]
                for concept, keys in BALANCE_SHEET_MAP:
                    facts.append((concept, 'instant', None, record.date_to, sum(totals[k] for k in keys)))
            elif kind == 'cash_flow':
                figures = wizard._nc_cash_flow_figures(record)
                for concept, key in CASH_FLOW_DURATION:
                    facts.append((concept, 'duration', record.date_from, record.date_to, figures[key]))
                opening = record.date_from - relativedelta(days=1)
                facts.append(('CashAndCashEquivalents', 'instant', None, opening, figures['cash_open']))
                facts.append(('CashAndCashEquivalents', 'instant', None, record.date_to, figures['cash_close']))
            elif kind == 'equity_changes':
                record._nc_assert_whole_entity()
                figures = record._nc_equity_figures()[0]
                opening = record.date_from - relativedelta(days=1)
                facts.append(('Equity', 'instant', None, opening, figures['total'][0]))
                facts.append(('ProfitLoss', 'duration', record.date_from, record.date_to,
                              figures['accumulated_result'][1]))
                facts.append(('ChangesInEquity', 'duration', record.date_from, record.date_to,
                              figures['total'][1]))
                facts.append(('Equity', 'instant', None, record.date_to, figures['total'][2]))
            else:
                figures = wizard._nc_pl_figures(record)[0]
                if not record.date_from:
                    raise UserError(self.env._("A Profit and Loss needs a start date to be exported as XBRL."))
                for concept, key in PROFIT_LOSS_MAP:
                    facts.append((concept, 'duration', record.date_from, record.date_to, figures[key]))
        return facts

    @api.model
    def _nc_entity_identifier(self, company):
        """The company's tax registration (TRN / VAT) when it has one, otherwise a stable internal identifier.
        Plain token characters only (XBRL token)."""
        value = re.sub(r'[^A-Za-z0-9._-]', '', company.vat or '') or 'company-%d' % company.id
        return value[:64]

    @api.model
    def _nc_build(self, wizard):
        """The XBRL instance (bytes, UTF-8). Read with the user's own rights: the caller checked access."""
        wizard.ensure_one()
        company = wizard.company_id or self.env.company
        currency = company.currency_id
        facts = self._nc_facts(wizard)
        root = etree.Element('{%s}xbrl' % NS['xbrli'], nsmap=NS)
        etree.SubElement(root, '{%s}schemaRef' % NS['link'], {
            '{%s}type' % NS['xlink']: 'simple', '{%s}href' % NS['xlink']: TAXONOMY_ENTRY_POINT})
        identifier = self._nc_entity_identifier(company)
        contexts = {}

        def context_id(period_type, start, end):
            key = (period_type, start, end)
            if key in contexts:
                return contexts[key]
            cid = ('I' + end.isoformat()) if period_type == 'instant' else ('D%s_%s' % (start.isoformat(),
                                                                                        end.isoformat()))
            cid = 'c_' + cid.replace('-', '')
            ctx = etree.SubElement(root, '{%s}context' % NS['xbrli'], {'id': cid})
            entity = etree.SubElement(ctx, '{%s}entity' % NS['xbrli'])
            etree.SubElement(entity, '{%s}identifier' % NS['xbrli'], {'scheme': ENTITY_SCHEME}).text = identifier
            period = etree.SubElement(ctx, '{%s}period' % NS['xbrli'])
            if period_type == 'instant':
                etree.SubElement(period, '{%s}instant' % NS['xbrli']).text = end.isoformat()
            else:
                etree.SubElement(period, '{%s}startDate' % NS['xbrli']).text = start.isoformat()
                etree.SubElement(period, '{%s}endDate' % NS['xbrli']).text = end.isoformat()
            contexts[key] = cid
            return cid

        for concept, period_type, start, end, _value in facts:
            context_id(period_type, start, end)
        unit_id = 'u_' + currency.name
        unit = etree.SubElement(root, '{%s}unit' % NS['xbrli'], {'id': unit_id})
        etree.SubElement(unit, '{%s}measure' % NS['xbrli']).text = 'iso4217:' + currency.name
        decimals = currency.decimal_places
        for concept, period_type, start, end, value in facts:
            fact = etree.SubElement(root, '{%s}%s' % (NS['ifrs-full'], concept), {
                'contextRef': contexts[(period_type, start, end)], 'unitRef': unit_id, 'decimals': str(decimals)})
            fact.text = '{:.{p}f}'.format(currency.round(value) + 0.0, p=decimals)
        return etree.tostring(root, xml_declaration=True, encoding='UTF-8', pretty_print=True)
