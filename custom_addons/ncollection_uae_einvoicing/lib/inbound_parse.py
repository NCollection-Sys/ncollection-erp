# -*- coding: utf-8 -*-
"""Read a RECEIVED PINT-AE (UBL 2.1) invoice or credit note into a plain dict. Pure: no Odoo, no network.

Safe parsing: no DTD, no entity resolution, no network, no huge trees; a document that declares a DOCTYPE is refused
outright (XXE and entity expansion are not merely disabled, they are rejected). Only the facts the inbound engine needs
are read; nothing is guessed: a missing element is ``None`` and the checks (:func:`check`) say what is missing.
"""

from decimal import Decimal, InvalidOperation

from lxml import etree

NS = {
    'inv': 'urn:oasis:names:specification:ubl:schema:xsd:Invoice-2',
    'cn': 'urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2',
    'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2',
    'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2',
}
ROOTS = {'{%s}Invoice' % NS['inv']: 'invoice', '{%s}CreditNote' % NS['cn']: 'credit_note'}
MAX_BYTES = 10 * 1024 * 1024
TOLERANCE = Decimal('0.01')
# UNTDID 1001 codes PINT-AE uses. 389 / 261 are SELF-BILLED: the BUYER issued them on behalf of its supplier, so for
# the tenant that RECEIVES one (the supplier) it is a SALE document: a DRAFT customer invoice, never a vendor bill.
# Self-billing has no commercial (480 / 81) form (Guidelines V2.0 §10.3).
PURCHASE_TYPES = {'invoice': {'380', '480'}, 'credit_note': {'381', '81'}}
SELF_BILLED_BY_KIND = {'invoice': {'389'}, 'credit_note': {'261'}}
SELF_BILLED_TYPES = {'389', '261'}


class InboundError(ValueError):
    """A document that cannot be read at all. ``code`` is stable (it becomes the rule id of the finding)."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _parser():
    return etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, dtd_validation=False,
                           huge_tree=False, remove_comments=True, remove_pis=True)


def _text(node, path):
    found = node.find(path, NS)
    if found is None or found.text is None:
        return None
    return found.text.strip() or None


def _attr(node, path, name):
    found = node.find(path, NS)
    return None if found is None else (found.get(name) or None)


def _amount(node, path):
    value = _text(node, path)
    if value is None:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        raise InboundError('bad_amount', 'an amount is not a number: %s' % path) from None


def _party(party):
    if party is None:
        return {}
    return {
        'endpoint_scheme': _attr(party, 'cbc:EndpointID', 'schemeID'),
        'endpoint_id': _text(party, 'cbc:EndpointID'),
        'trn': _text(party, 'cac:PartyTaxScheme/cbc:CompanyID'),
        'name': _text(party, 'cac:PartyLegalEntity/cbc:RegistrationName'),
        'legal_id': _text(party, 'cac:PartyLegalEntity/cbc:CompanyID'),
    }


def parse(xml):
    """The facts of one received document, or :class:`InboundError` when it cannot be read safely."""
    if not isinstance(xml, bytes) or not xml:
        raise InboundError('empty', 'no document')
    if len(xml) > MAX_BYTES:
        raise InboundError('too_large', 'the document is larger than %d bytes' % MAX_BYTES)
    if b'<!DOCTYPE' in xml[:4096].upper() or b'<!ENTITY' in xml.upper():
        raise InboundError('doctype', 'a document type declaration is not accepted')
    try:
        root = etree.fromstring(xml, parser=_parser())
    except etree.XMLSyntaxError:
        raise InboundError('malformed', 'the document is not well-formed XML') from None
    kind = ROOTS.get(root.tag)
    if not kind:
        raise InboundError('not_ubl', 'the document is not a UBL invoice or credit note')
    type_path = 'cbc:InvoiceTypeCode' if kind == 'invoice' else 'cbc:CreditNoteTypeCode'
    line_tag, qty_tag = ('cac:InvoiceLine', 'cbc:InvoicedQuantity') if kind == 'invoice' else \
        ('cac:CreditNoteLine', 'cbc:CreditedQuantity')
    lines = []
    for line in root.findall(line_tag, NS):
        lines.append({
            'id': _text(line, 'cbc:ID'),
            'name': _text(line, 'cac:Item/cbc:Name'),
            'quantity': _amount(line, qty_tag),
            'unit_code': _attr(line, qty_tag, 'unitCode'),
            'net_amount': _amount(line, 'cbc:LineExtensionAmount'),
            'price': _amount(line, 'cac:Price/cbc:PriceAmount'),
            'base_quantity': _amount(line, 'cac:Price/cbc:BaseQuantity'),
            'tax_category': _text(line, 'cac:Item/cac:ClassifiedTaxCategory/cbc:ID'),
            'tax_percent': _amount(line, 'cac:Item/cac:ClassifiedTaxCategory/cbc:Percent'),
        })
    tax_totals = root.findall('cac:TaxTotal', NS)
    currency = _text(root, 'cbc:DocumentCurrencyCode')
    tax_total = None
    for total in tax_totals:
        amount = total.find('cbc:TaxAmount', NS)
        if amount is not None and (amount.get('currencyID') or currency) == currency:
            tax_total = _amount(total, 'cbc:TaxAmount')
    return {
        'kind': kind,
        'type_code': _text(root, type_path),
        'number': _text(root, 'cbc:ID'),
        'uuid': _text(root, 'cbc:UUID'),
        'issue_date': _text(root, 'cbc:IssueDate'),
        'due_date': _text(root, 'cbc:DueDate'),
        'currency': currency,
        'seller': _party(root.find('cac:AccountingSupplierParty/cac:Party', NS)),
        'buyer': _party(root.find('cac:AccountingCustomerParty/cac:Party', NS)),
        'billing_reference': _text(root, 'cac:BillingReference/cac:InvoiceDocumentReference/cbc:ID'),
        'line_total': _amount(root, 'cac:LegalMonetaryTotal/cbc:LineExtensionAmount'),
        'tax_exclusive': _amount(root, 'cac:LegalMonetaryTotal/cbc:TaxExclusiveAmount'),
        'tax_inclusive': _amount(root, 'cac:LegalMonetaryTotal/cbc:TaxInclusiveAmount'),
        'payable': _amount(root, 'cac:LegalMonetaryTotal/cbc:PayableAmount'),
        'allowance_total': _amount(root, 'cac:LegalMonetaryTotal/cbc:AllowanceTotalAmount') or Decimal(0),
        'charge_total': _amount(root, 'cac:LegalMonetaryTotal/cbc:ChargeTotalAmount') or Decimal(0),
        'prepaid': _amount(root, 'cac:LegalMonetaryTotal/cbc:PrepaidAmount') or Decimal(0),
        'tax_total': tax_total,
        'lines': lines,
    }


def check(facts):
    """``[(code, message)]`` of what makes ``facts`` unusable as a vendor bill source. Empty = structurally sound.
    The OFFICIAL rules are the validator's (lib/validator.py); these are the inbound engine's own preconditions."""
    found = []
    for key, code in (('number', 'missing_number'), ('issue_date', 'missing_issue_date'),
                      ('currency', 'missing_currency'), ('type_code', 'missing_type_code')):
        if not facts.get(key):
            found.append((code, 'the document has no %s' % key.replace('_', ' ')))
    seller = facts.get('seller') or {}
    if not (seller.get('trn') or seller.get('endpoint_id') or seller.get('legal_id')):
        found.append(('missing_supplier_id', 'the supplier carries no TRN, endpoint or legal registration'))
    if not facts.get('lines'):
        found.append(('no_lines', 'the document has no lines'))
    for line in facts.get('lines') or ():
        if line['quantity'] is None or line['net_amount'] is None or not line['tax_category']:
            found.append(('incomplete_line', 'line %s lacks its quantity, amount or tax category' % line['id']))
    if facts.get('kind') == 'credit_note' and not facts.get('billing_reference'):
        found.append(('missing_reference', 'a credit note must reference the invoice it corrects'))
    lines_sum = sum((line['net_amount'] or Decimal(0)) for line in facts.get('lines') or ())
    if facts.get('line_total') is None or abs(lines_sum - facts['line_total']) > TOLERANCE:
        found.append(('line_total_mismatch', 'the lines do not add up to the line total'))
    exclusive, inclusive, tax = facts.get('tax_exclusive'), facts.get('tax_inclusive'), facts.get('tax_total')
    if exclusive is None or inclusive is None or tax is None:
        found.append(('missing_totals', 'the document totals are incomplete'))
    else:
        expected_exclusive = (facts.get('line_total') or Decimal(0)) - facts['allowance_total'] + facts['charge_total']
        if abs(expected_exclusive - exclusive) > TOLERANCE:
            found.append(('exclusive_mismatch', 'the tax-exclusive total does not follow from the lines'))
        if abs(exclusive + tax - inclusive) > TOLERANCE:
            found.append(('inclusive_mismatch', 'tax-exclusive total plus tax is not the tax-inclusive total'))
        if facts.get('payable') is not None and abs(inclusive - facts['prepaid'] - facts['payable']) > TOLERANCE:
            found.append(('payable_mismatch', 'the payable amount does not follow from the totals'))
    return found
