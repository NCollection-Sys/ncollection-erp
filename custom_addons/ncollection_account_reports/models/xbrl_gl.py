# -*- coding: utf-8 -*-
"""Trial Balance as an XBRL Global Ledger instance (XBRL GL 2015-03-25 Recommendation, Palette case-c-b-m-u-t).

WHY XBRL GL AND NOT IFRS. A trial balance is a list of the company's OWN accounts. The IFRS Accounting Taxonomy has
no concept per ledger account, so an IFRS instance would have to invent a mapping. XBRL GL is XBRL International's
taxonomy for exactly this data: the account is DATA (``gl-cor:accountMainID`` / ``accountMainDescription``), not a
concept, and ``entriesType`` has an enumerated value ``trialbalance``.

THE SHAPE follows XBRL International's own best-practice sample ``ids/BP_TrialBalance.xml`` (same Recommendation):
per account one ``entryDetail`` for the beginning balance (``xbrlInclude`` = ``beginning_balance``), one for the
period debits and one for the period credits (``period_change``, debit / credit code D / C) and one for the ending
balance (``ending_balance``). Amounts are never negative: the sign is the debit / credit code, as in the sample.

THE FIGURES are the report's own rows (``_nc_compute_lines``: the same filters, the same code path as the screen, the
PDF and the Excel file), so the file cannot disagree with what the user exported. Read with the user's own rights;
nothing is written.

WHAT THIS IS NOT. A regulatory filing: XBRL GL carries ledger data between systems and to auditors; no regulator
profile is applied (docs/markdown/XBRL_EXPORT.md).
"""
from lxml import etree

from odoo import fields

GL_ENTRY_POINT = ('http://www.xbrl.org/taxonomy/int/gl/2015-03-25/plt/case-c-b-m-u-t/'
                  'gl-plt-all-2015-03-25.xsd')
GL_NS = {
    'xbrli': 'http://www.xbrl.org/2003/instance',
    'link': 'http://www.xbrl.org/2003/linkbase',
    'xlink': 'http://www.w3.org/1999/xlink',
    'iso4217': 'http://www.xbrl.org/2003/iso4217',
    'iso639': 'http://www.xbrl.org/2005/iso639',
    'gl-cor': 'http://www.xbrl.org/int/gl/cor/2015-03-25',
    'gl-bus': 'http://www.xbrl.org/int/gl/bus/2015-03-25',
    'gl-muc': 'http://www.xbrl.org/int/gl/muc/2015-03-25',
}
ENTITY_SCHEME = 'https://www.ncollection.com/xbrl/entity'
CTX = 'now'


def _el(parent, prefix, name, text=None, **attrs):
    node = etree.SubElement(parent, '{%s}%s' % (GL_NS[prefix], name), attrs)
    if text is not None:
        node.text = text
    return node


def _item(parent, prefix, name, text, **attrs):
    return _el(parent, prefix, name, text, contextRef=CTX, **attrs)


def trial_balance_rows(wizard):
    """The report's account rows (without its Total row), exactly as the screen shows them."""
    return [row for row in wizard._nc_compute_lines() if row.get('account_id')]


def filter_summary(wizard):
    """The report's filters in words, written into ``entriesComment`` so the file says what it was made from."""
    parts = []
    if wizard.target_move:
        parts.append('entries: %s' % wizard.target_move)
    for name, label in (('journal_ids', 'journals'), ('account_ids', 'accounts'), ('partner_ids', 'partners')):
        records = wizard[name] if name in wizard._fields else None
        if records:
            parts.append('%s: %s' % (label, ', '.join(records.mapped('display_name'))))
    if 'exclude_zero' in wizard._fields:
        parts.append('zero rows hidden' if wizard.exclude_zero else 'zero rows shown')
    return '; '.join(parts) or 'no filter'


class GlDocument:
    """One XBRL GL instance: the head every export shares (schema, the conventional context, the unit, documentInfo,
    entityInformation), then entry headers and entry details written in the order the gl-cor schema requires
    (entryDetail: lineNumber, account, amount, debitCreditCode, postingDate, identifierReference, documentNumber,
    xbrlInfo, detailComment)."""

    def __init__(self, xbrl_model, wizard, entries_type, unique_prefix):
        wizard.ensure_one()
        self.env = env = xbrl_model.env
        self.wizard = wizard
        company = wizard.company_id or env.company
        self.currency = currency = company.currency_id
        self.decimals = currency.decimal_places
        today = fields.Date.context_today(wizard)
        self.root = root = etree.Element('{%s}xbrl' % GL_NS['xbrli'], nsmap=GL_NS)
        _el(root, 'link', 'schemaRef', **{'{%s}type' % GL_NS['xlink']: 'simple',
                                          '{%s}href' % GL_NS['xlink']: GL_ENTRY_POINT})
        # XBRL GL: the context is required by XBRL 2.1 but does not describe the data; by the Recommendation's
        # convention every fact uses one instant context dated the day the file is created.
        ctx = _el(root, 'xbrli', 'context', id=CTX)
        entity = _el(ctx, 'xbrli', 'entity')
        _el(entity, 'xbrli', 'identifier', xbrl_model._nc_entity_identifier(company), scheme=ENTITY_SCHEME)
        period = _el(ctx, 'xbrli', 'period')
        _el(period, 'xbrli', 'instant', today.isoformat())
        self.unit_id = 'u_' + currency.name
        unit = _el(root, 'xbrli', 'unit', id=self.unit_id)
        _el(unit, 'xbrli', 'measure', 'iso4217:' + currency.name)

        self.entries = entries = _el(root, 'gl-cor', 'accountingEntries')
        info = _el(entries, 'gl-cor', 'documentInfo')
        _item(info, 'gl-cor', 'entriesType', entries_type)
        date_from = wizard.date_from.isoformat() if wizard.date_from else 'start'
        _item(info, 'gl-cor', 'uniqueID', '%s-%d-%s-%s' % (unique_prefix, company.id, date_from,
                                                           wizard.date_to.isoformat()))
        lang = (env.lang or 'en_US').split('_')[0].lower()
        _item(info, 'gl-cor', 'language', 'iso639:' + (lang if len(lang) == 2 else 'en'))
        _item(info, 'gl-cor', 'creationDate', today.isoformat())
        _item(info, 'gl-bus', 'creator', company.name)
        _item(info, 'gl-cor', 'entriesComment', '%s: %s' % (wizard._nc_report_title(), filter_summary(wizard)))
        if wizard.date_from:
            _item(info, 'gl-cor', 'periodCoveredStart', wizard.date_from.isoformat())
        _item(info, 'gl-cor', 'periodCoveredEnd', wizard.date_to.isoformat())
        # the system's configured name (System Branding), never a literal; plain Odoo when branding is absent
        branding = env.get('ncollection.product.branding')
        brand = branding.nc_brand().get('name') if branding is not None else ''
        _item(info, 'gl-bus', 'sourceApplication', '%s (Odoo 19)' % brand if brand else 'Odoo 19')
        _item(info, 'gl-muc', 'defaultCurrency', 'iso4217:' + currency.name)

        entity_info = _el(entries, 'gl-cor', 'entityInformation')
        ids = _el(entity_info, 'gl-bus', 'organizationIdentifiers')
        _item(ids, 'gl-bus', 'organizationIdentifier', company.name)
        _item(ids, 'gl-bus', 'organizationDescription', 'Company name')
        if company.vat:
            ids = _el(entity_info, 'gl-bus', 'organizationIdentifiers')
            _item(ids, 'gl-bus', 'organizationIdentifier', company.vat)
            _item(ids, 'gl-bus', 'organizationDescription', 'Tax registration number')
        self.line = 0

    def header(self, comment=None):
        node = _el(self.entries, 'gl-cor', 'entryHeader')
        if comment:
            _item(node, 'gl-cor', 'entryComment', comment[:1000])
        return node

    def signed_code(self, value):
        return 'C' if self.currency.round(value) < 0 else 'D'

    def detail(self, header, account, value, code, include=None, posting_date=None, partner=None,
               document=None, comment=None):
        self.line += 1
        node = _el(header, 'gl-cor', 'entryDetail')
        _item(node, 'gl-cor', 'lineNumber', str(self.line))
        if account:
            acc = _el(node, 'gl-cor', 'account')
            _item(acc, 'gl-cor', 'accountMainID', account.code or str(account.id))
            _item(acc, 'gl-cor', 'accountMainDescription', account.name or account.code or '')
            _item(acc, 'gl-cor', 'accountType', 'account')
        _item(node, 'gl-cor', 'amount', '{:.{p}f}'.format(abs(self.currency.round(value)) + 0.0, p=self.decimals),
              unitRef=self.unit_id, decimals=str(self.decimals))
        _item(node, 'gl-cor', 'debitCreditCode', code)
        if posting_date:
            _item(node, 'gl-cor', 'postingDate', posting_date.isoformat())
        if partner:
            ref = _el(node, 'gl-cor', 'identifierReference')
            _item(ref, 'gl-cor', 'identifierCode', partner.ref or str(partner.id))
            _item(ref, 'gl-cor', 'identifierDescription', partner.display_name or '')
        if document:
            _item(node, 'gl-cor', 'documentNumber', document)
        if include:
            xinfo = _el(node, 'gl-cor', 'xbrlInfo')
            _item(xinfo, 'gl-cor', 'xbrlInclude', include)
        if comment:
            _item(node, 'gl-cor', 'detailComment', comment[:1000])

    def tostring(self):
        return etree.tostring(self.root, xml_declaration=True, encoding='UTF-8', pretty_print=True)


def build_trial_balance(xbrl_model, wizard):
    """The XBRL GL instance (bytes, UTF-8) of ``wizard`` (a Trial Balance run), shaped like BP_TrialBalance.xml."""
    doc = GlDocument(xbrl_model, wizard, 'trialbalance', 'TB')
    rows = trial_balance_rows(wizard)
    by_id = {a.id: a for a in doc.env['account.account'].browse([row['account_id'] for row in rows])}
    header = doc.header()
    for row in rows:
        account = by_id[row['account_id']]
        doc.detail(header, account, row['opening_balance'], doc.signed_code(row['opening_balance']),
                   'beginning_balance', wizard.date_from)
        doc.detail(header, account, row['debit'], 'D', 'period_change')
        doc.detail(header, account, row['credit'], 'C', 'period_change')
        doc.detail(header, account, row['closing_balance'], doc.signed_code(row['closing_balance']),
                   'ending_balance', wizard.date_to)
    return doc.tostring()


def build_ledger(xbrl_model, wizard, group_by):
    """The General Ledger (``group_by`` = 'account_id') or the Partner Ledger ('partner_id') as an XBRL GL
    ``ledger``: per group of the report, one entryHeader with the beginning balance, every journal item of the period
    (account, partner, entry number, posting date, label; the amount as debit or credit) and the ending balance,
    which is the report's own running balance after its last item. Exactly the report's rows, nothing re-queried."""
    doc = GlDocument(xbrl_model, wizard, 'ledger', 'GL' if group_by == 'account_id' else 'PL')
    env = doc.env
    rows = wizard._nc_compute_lines()
    accounts = {a.id: a for a in env['account.account'].browse(
        list({r['account_id'] for r in rows if r.get('account_id')}))}
    partners = {p.id: p for p in env['res.partner'].browse(
        list({r['partner_id'] for r in rows if r.get('partner_id')}))}
    groups, order = {}, []
    for row in rows:
        key = row.get(group_by) or False
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)
    for key in order:
        group = groups[key]
        opening = group[0] if group[0].get('is_initial') else None
        items = [r for r in group if not r.get('is_initial')]
        label = (opening or {}).get('label') or ''
        header = doc.header(label.split('—', 1)[-1].strip() if label else None)
        group_account = accounts.get(key) if group_by == 'account_id' else None
        group_partner = partners.get(key) if group_by == 'partner_id' else None
        start = opening['running_balance'] if opening else 0.0
        doc.detail(header, group_account, start, doc.signed_code(start), 'beginning_balance', wizard.date_from,
                   partner=group_partner)
        for row in items:
            debit, credit = row.get('debit') or 0.0, row.get('credit') or 0.0
            doc.detail(header, accounts.get(row.get('account_id')), debit or credit, 'D' if debit else 'C',
                       None, row.get('date') or None, partner=partners.get(row.get('partner_id')),
                       document=row.get('move_name') or None, comment=row.get('label') or None)
        end = items[-1]['running_balance'] if items else start
        doc.detail(header, group_account, end, doc.signed_code(end), 'ending_balance', wizard.date_to,
                   partner=group_partner)
    return doc.tostring()
