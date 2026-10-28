# -*- coding: utf-8 -*-
"""Which PINT-AE document type a document is: 380 / 480 (invoice) or 381 / 81 (credit note).

The rule (DECISION 2026-10-28, recorded as upstream finding ``exempt-only-tax-invoice-refused`` and OCQ-20):

* A document with at least one line in a VAT-relevant category (S, Z, AE or N) is an electronic Tax Invoice 380 / Tax
  Credit Note 381. A zero-rated supply therefore stays a tax document (Guidelines V2.0 App 6 §18.3.1: 480 is not for
  zero-rated supplies; the fatal rule ibr-151-ae is satisfied by a 'Z' line).
* A document whose every line is Exempt (E) and/or Not subject to VAT (O) is 480 / 81. The fatal rule ibr-151-ae
  refuses a 380 / 381 made only of E / O lines, so such a document cannot be a tax document under the pinned PINT-AE
  release, even though Guidelines V2.0 App 6 §18.3.1 says 480 should not be used for exempt supplies: the technical
  rule the network validates wins, and the conflict stays open.
* 480 / 81 may carry only E, O or Z (fatal ibr-122-ae); with the rule above 480 / 81 never carries Z.

Self-billing codes (389 / 261) belong to PINT AE Self-Billing and are never produced here.
"""

TAX_RELEVANT = frozenset({'S', 'Z', 'AE', 'N'})
NON_TAX = frozenset({'E', 'O'})


def type_code(kind, categories, profile):
    """The type code for a document of ``kind`` ('invoice' | 'credit_note') whose lines use ``categories``."""
    used = set(categories) - {None}
    non_tax_only = bool(used) and used <= NON_TAX
    if kind == 'credit_note':
        return profile.credit_type_out_of_scope if non_tax_only else profile.credit_type_tax
    return profile.invoice_type_out_of_scope if non_tax_only else profile.invoice_type_tax
