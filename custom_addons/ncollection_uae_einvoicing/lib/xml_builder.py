# -*- coding: utf-8 -*-
"""PINT-AE UBL 2.1 XML from a reconciled payload.

The builder is a PURE SERIALISER. It computes nothing: every amount is taken as given (the payload's ``figures`` are
Odoo's booked values and the lines/adjustments carry Odoo's amounts), formatted exactly. Element names, order and
structure follow the official samples of the pinned release (UBL 2.1 sequence), so the output is what the official
Schematron expects; the Schematron (lib/validator.py) remains the judge.

``payload`` keys used: ``kind`` (invoice|credit_note), ``type_code``, ``number``, ``uuid``, ``issue_date``,
``issue_time``, ``due_date``, ``vat_point_date``, ``note``, ``currency``, ``exchange_rate``, ``transaction_type_code``,
``credit_reason``, ``preceding`` ([{number, date}]), ``billing_frequency``, ``period_start``, ``period_end``,
``beneficiary_id``, ``principal_id``, ``seller``/``buyer`` (party dicts), ``payment`` ({means_code, account_id,
terms_note}), ``lines``, ``adjustments``, ``figures`` (reconcile output), ``aed`` ({tax_total, total_incl} for non-AED
documents).
"""

from decimal import Decimal

from lxml import etree

from .money import fmt_amount, fmt_plain
from .spec import get_profile

NS_CAC = 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2'
NS_CBC = 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2'
NS_INVOICE = 'urn:oasis:names:specification:ubl:schema:xsd:Invoice-2'
NS_CREDIT = 'urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2'


def _q(prefix, name):
    return '{%s}%s' % ({'cac': NS_CAC, 'cbc': NS_CBC}[prefix], name)


def _add(parent, qname, text=None, **attrs):
    prefix, name = qname.split(':')
    element = etree.SubElement(parent, _q(prefix, name))
    if text is not None:
        element.text = str(text)
    for key, value in attrs.items():
        if value is not None:
            element.set(key, str(value))
    return element


def _amount(parent, qname, value, currency):
    return _add(parent, qname, fmt_amount(value), currencyID=currency)


def build(payload, profile=None):
    """The serialised document as UTF-8 bytes."""
    profile = profile or get_profile()
    is_credit = payload['kind'] == 'credit_note'
    ns = NS_CREDIT if is_credit else NS_INVOICE
    root = etree.Element(
        '{%s}%s' % (ns, 'CreditNote' if is_credit else 'Invoice'), nsmap={None: ns, 'cac': NS_CAC, 'cbc': NS_CBC}
    )
    currency = payload['currency']
    is_foreign = currency != 'AED'

    _add(root, 'cbc:CustomizationID', profile.customization_id)
    _add(root, 'cbc:ProfileID', profile.profile_id)
    _add(root, 'cbc:ProfileExecutionID', payload['transaction_type_code'])
    _add(root, 'cbc:ID', payload['number'])
    _add(root, 'cbc:UUID', payload['uuid'])
    _add(root, 'cbc:IssueDate', payload['issue_date'])
    if payload.get('issue_time'):
        _add(root, 'cbc:IssueTime', payload['issue_time'])
    if payload.get('due_date') and not is_credit:
        _add(root, 'cbc:DueDate', payload['due_date'])
    _add(root, 'cbc:CreditNoteTypeCode' if is_credit else 'cbc:InvoiceTypeCode', payload['type_code'])
    if payload.get('note'):
        _add(root, 'cbc:Note', payload['note'])
    if payload.get('vat_point_date') and not is_credit:
        _add(root, 'cbc:TaxPointDate', payload['vat_point_date'])
    _add(root, 'cbc:DocumentCurrencyCode', currency)
    if is_foreign:
        _add(root, 'cbc:TaxCurrencyCode', 'AED')
    if payload.get('period_start') or payload.get('period_end') or payload.get('billing_frequency'):
        period = _add(root, 'cac:InvoicePeriod')
        if payload.get('period_start'):
            _add(period, 'cbc:StartDate', payload['period_start'])
        if payload.get('period_end'):
            _add(period, 'cbc:EndDate', payload['period_end'])
        if payload.get('billing_frequency'):
            _add(period, 'cbc:Description', payload['billing_frequency'])
    if is_credit:
        response = _add(root, 'cac:DiscrepancyResponse')
        _add(response, 'cbc:ResponseCode', payload['credit_reason'])
    for ref in payload.get('preceding', []):
        billing = _add(root, 'cac:BillingReference')
        document = _add(billing, 'cac:InvoiceDocumentReference')
        _add(document, 'cbc:ID', ref['number'])
        if ref.get('date'):
            _add(document, 'cbc:IssueDate', ref['date'])
    if is_foreign and payload.get('aed'):
        reference = _add(root, 'cac:AdditionalDocumentReference')
        _add(reference, 'cbc:ID', 'AED')
        _add(reference, 'cbc:DocumentTypeCode', 'aedtotal-incl-vat')
        _add(reference, 'cbc:DocumentDescription', fmt_amount(payload['aed']['total_incl']))

    _party(root, 'cac:AccountingSupplierParty', payload['seller'], seller=True)
    _party(root, 'cac:AccountingCustomerParty', payload['buyer'], seller=False)
    if payload.get('beneficiary_id'):
        beneficiary = _add(_add(root, 'cac:BuyerCustomerParty'), 'cac:Party')
        _add(_add(beneficiary, 'cac:PartyIdentification'), 'cbc:ID', payload['beneficiary_id'])
    if payload.get('principal_id'):
        principal = _add(_add(root, 'cac:SellerSupplierParty'), 'cac:Party')
        _add(_add(principal, 'cac:PartyIdentification'), 'cbc:ID', payload['principal_id'])

    payment = payload.get('payment') or {}
    if payment.get('means_code'):
        means = _add(root, 'cac:PaymentMeans')
        _add(means, 'cbc:PaymentMeansCode', payment['means_code'])
        if payment.get('account_id'):
            account = _add(means, 'cac:PayeeFinancialAccount')
            _add(account, 'cbc:ID', payment['account_id'])
    if payment.get('terms_note'):
        _add(_add(root, 'cac:PaymentTerms'), 'cbc:Note', payment['terms_note'])

    for adjustment in payload.get('adjustments', []):
        _adjustment(root, adjustment, currency)

    if is_foreign:
        rate = _add(root, 'cac:TaxExchangeRate')
        _add(rate, 'cbc:SourceCurrencyCode', currency)
        _add(rate, 'cbc:TargetCurrencyCode', 'AED')
        _add(rate, 'cbc:CalculationRate', fmt_plain(payload['exchange_rate']))

    figures = payload['figures']
    total = _add(root, 'cac:TaxTotal')
    _amount(total, 'cbc:TaxAmount', figures['tax_total'], currency)
    for entry in figures['breakdown']:
        sub = _add(total, 'cac:TaxSubtotal')
        _amount(sub, 'cbc:TaxableAmount', entry['taxable'], currency)
        _amount(sub, 'cbc:TaxAmount', entry['tax'], currency)
        _tax_category(sub, entry['category'], entry['rate'])
    if is_foreign and payload.get('aed'):
        aed_total = _add(root, 'cac:TaxTotal')
        _amount(aed_total, 'cbc:TaxAmount', payload['aed']['tax_total'], 'AED')

    monetary = _add(root, 'cac:LegalMonetaryTotal')
    _amount(monetary, 'cbc:LineExtensionAmount', figures['line_extension'], currency)
    _amount(monetary, 'cbc:TaxExclusiveAmount', figures['tax_exclusive'], currency)
    _amount(monetary, 'cbc:TaxInclusiveAmount', figures['tax_inclusive'], currency)
    if figures.get('allowance_total') is not None:
        _amount(monetary, 'cbc:AllowanceTotalAmount', figures['allowance_total'], currency)
    if figures.get('charge_total') is not None:
        _amount(monetary, 'cbc:ChargeTotalAmount', figures['charge_total'], currency)
    if figures.get('prepaid') is not None:
        _amount(monetary, 'cbc:PrepaidAmount', figures['prepaid'], currency)
    if figures.get('rounding') is not None:
        _amount(monetary, 'cbc:PayableRoundingAmount', figures['rounding'], currency)
    _amount(monetary, 'cbc:PayableAmount', figures['payable'], currency)

    line_tag = 'cac:CreditNoteLine' if is_credit else 'cac:InvoiceLine'
    qty_tag = 'cbc:CreditedQuantity' if is_credit else 'cbc:InvoicedQuantity'
    for line in payload['lines']:
        _line(root, line, line_tag, qty_tag, currency)
    return etree.tostring(root, xml_declaration=True, encoding='UTF-8', pretty_print=True)


# ---------------------------------------------------------------------------------------------------------------------
def _tax_category(parent, category, rate, exemption_code=None):
    node = _add(parent, 'cac:TaxCategory')
    _add(node, 'cbc:ID', category)
    if rate is not None:
        _add(node, 'cbc:Percent', fmt_plain(rate))
    if exemption_code:
        _add(node, 'cbc:TaxExemptionReasonCode', exemption_code)
    _add(_add(node, 'cac:TaxScheme'), 'cbc:ID', 'VAT')
    return node


def _party(root, tag, party, seller):
    wrapper = _add(root, tag)
    node = _add(wrapper, 'cac:Party')
    _add(node, 'cbc:EndpointID', party['endpoint_id'], schemeID=party['endpoint_scheme'])
    if party.get('trading_name'):
        _add(_add(node, 'cac:PartyName'), 'cbc:Name', party['trading_name'])
    address = _add(node, 'cac:PostalAddress')
    _add(address, 'cbc:StreetName', party['street'])
    if party.get('street2'):
        _add(address, 'cbc:AdditionalStreetName', party['street2'])
    _add(address, 'cbc:CityName', party['city'])
    if party.get('postal'):
        _add(address, 'cbc:PostalZone', party['postal'])
    _add(address, 'cbc:CountrySubentity', party['subdivision'])
    _add(_add(address, 'cac:Country'), 'cbc:IdentificationCode', party['country'])
    if party.get('trn'):
        scheme = _add(node, 'cac:PartyTaxScheme')
        _add(scheme, 'cbc:CompanyID', party['trn'])
        _add(_add(scheme, 'cac:TaxScheme'), 'cbc:ID', 'VAT')
    elif seller and party.get('tin'):
        # a seller without a TRN: its TIN is the tax registration identifier, scheme TIN (official Seller-TIN sample)
        scheme = _add(node, 'cac:PartyTaxScheme')
        _add(scheme, 'cbc:CompanyID', party['tin'])
        _add(_add(scheme, 'cac:TaxScheme'), 'cbc:ID', 'TIN')
    legal = _add(node, 'cac:PartyLegalEntity')
    _add(legal, 'cbc:RegistrationName', party['name'])
    if party.get('legal_id'):
        agency_name = party.get('legal_country') if party.get('legal_type') == 'PAS' else party.get('legal_authority')
        _add(
            legal,
            'cbc:CompanyID',
            party['legal_id'],
            schemeAgencyID=party.get('legal_type'),
            schemeAgencyName=agency_name,
        )
    return node


def _adjustment(root, adjustment, currency):
    node = _add(root, 'cac:AllowanceCharge')
    _add(node, 'cbc:ChargeIndicator', 'true' if adjustment['charge'] else 'false')
    if adjustment.get('reason_code'):
        _add(node, 'cbc:AllowanceChargeReasonCode', adjustment['reason_code'])
    if adjustment.get('reason'):
        _add(node, 'cbc:AllowanceChargeReason', adjustment['reason'])
    if adjustment.get('percent') is not None:
        _add(node, 'cbc:MultiplierFactorNumeric', fmt_plain(adjustment['percent']))
    _amount(node, 'cbc:Amount', adjustment['amount'], currency)
    if adjustment.get('base_amount') is not None:
        _amount(node, 'cbc:BaseAmount', adjustment['base_amount'], currency)
    rate = adjustment.get('rate')
    if rate is None and adjustment['category'] == 'E':
        rate = Decimal(0)  # aligned-ibrp-e-06/07: an exempt document allowance / charge carries Percent 0 (O must not)
    _tax_category(
        node,
        adjustment['category'],
        rate,
        adjustment.get('exemption_code') if adjustment['category'] == 'E' else None,
    )


def _line(root, line, line_tag, qty_tag, currency):
    node = _add(root, line_tag)
    _add(node, 'cbc:ID', line['id'])
    _add(node, qty_tag, fmt_plain(line['qty']), unitCode=line['unit_code'])
    _amount(node, 'cbc:LineExtensionAmount', line['net'], currency)

    item = _add(node, 'cac:Item')
    _add(item, 'cbc:Description', line.get('description') or line['name'])
    _add(item, 'cbc:Name', line['name'])
    if line.get('seller_item_id'):
        _add(_add(item, 'cac:SellersItemIdentification'), 'cbc:ID', line['seller_item_id'])
    if line.get('gtin'):
        _add(_add(item, 'cac:StandardItemIdentification'), 'cbc:ID', line['gtin'], schemeID='0160')
    if line.get('sac'):
        _add(
            _add(item, 'cac:AdditionalItemIdentification'),
            'cbc:ID',
            line['sac'],
            schemeID='SAC',
            schemeVersionID='1.0',
        )
    if line.get('goods_type') or line.get('item_type') or line.get('hs'):
        classification = _add(item, 'cac:CommodityClassification')
        if line.get('goods_type'):
            _add(classification, 'cbc:NatureCode', line['goods_type'])
        if line.get('item_type'):
            _add(classification, 'cbc:CommodityCode', line['item_type'])
        if line.get('hs'):
            _add(classification, 'cbc:ItemClassificationCode', line['hs'], listID='HS', listVersionID='1.0')
    category = _add(item, 'cac:ClassifiedTaxCategory')
    _add(category, 'cbc:ID', line['category'])
    if line.get('rate') is not None:
        _add(category, 'cbc:Percent', fmt_plain(line['rate']))
    if line['category'] == 'E' and line.get('exemption_code'):
        _add(category, 'cbc:TaxExemptionReasonCode', line['exemption_code'])
    _add(_add(category, 'cac:TaxScheme'), 'cbc:ID', 'VAT')

    price = _add(node, 'cac:Price')
    _amount_plain(price, 'cbc:PriceAmount', line['price_net'], currency)
    _add(price, 'cbc:BaseQuantity', fmt_plain(line.get('base_qty') or 1), unitCode=line['unit_code'])
    # ibr-126-ae: the gross price (BT-148) and base quantity (BT-149) are MANDATORY on every line, so the price-level
    # AllowanceCharge is always written; with no discount its Amount is 0 (as in the official 'Mandatory fields'
    # sample).
    discount = _add(price, 'cac:AllowanceCharge')
    _add(discount, 'cbc:ChargeIndicator', 'false')
    _amount_plain(discount, 'cbc:Amount', line.get('price_discount') or 0, currency)
    _amount_plain(discount, 'cbc:BaseAmount', line.get('price_gross') or line['price_net'], currency)

    extension = _add(node, 'cac:ItemPriceExtension')
    _amount(extension, 'cbc:Amount', line['aed_amount'], 'AED')
    if line['category'] != 'E':  # ibr-163-ae: an exempt line carries no line VAT amount
        _amount(_add(extension, 'cac:TaxTotal'), 'cbc:TaxAmount', line['aed_vat'], 'AED')


def _amount_plain(parent, qname, value, currency):
    """A unit price: kept at the precision it was computed with (net = gross - discount must hold exactly)."""
    text = fmt_plain(value)
    if '.' not in text:
        text += '.00'
    elif len(text.split('.')[1]) < 2:
        text += '0'
    return _add(parent, qname, text, currencyID=currency)
