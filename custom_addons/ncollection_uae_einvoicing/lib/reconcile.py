# -*- coding: utf-8 -*-
"""Odoo accounting values → independent PINT-AE calculation → exact comparison → eligible / blocked.

ODOO IS THE SOURCE OF TRUTH. The figures that go into the XML are the figures Odoo booked (``figures`` below): the
document's untaxed/tax/total and, per VAT breakdown, the sum of the tax lines Odoo posted. This module recomputes the
PINT-AE quantities independently, from the lines, and compares. It NEVER feeds a recomputed value back into the XML,
never adjusts a rounding difference, and never "fixes" a total: when the booked figures cannot satisfy the official
rules, the document is BLOCKED with the reason, and nothing is changed anywhere.

The tolerances are not ours: they are exactly the ones in the pinned Schematron (so an official run agrees with this
pre-check):

* EXACT (no tolerance): ``ibr-147-ae`` line net = qty × net price (2 dp); ``ibr-co-13`` BT-109 = lines + charges −
  allowances; ``ibr-co-14`` BT-110 = Σ breakdown tax; ``ibr-co-15`` BT-112 = BT-109 + BT-110; ``ibr-co-16`` payable;
  ``aligned-ibrp-z-09 / e-09 / o-09 / ae-09-ae`` and ``ibr-108-ae``: tax of a Z/E/O/AE/N breakdown is zero;
  ``ibr-190-ae``: a Standard-rated rate is exactly the profile's standard rate;
* SLACK (± ``profile.tax_slack``, AED 0.02): ``aligned-ibrp-s-09`` Standard-rated breakdown tax = round(taxable × rate
  / 100).

Outcomes: ``exact`` (every difference zero), ``within_tolerance`` (some difference within the official slack: eligible,
and shown), ``blocked`` (any exact check fails or any slack is exceeded).
"""

from collections import namedtuple
from decimal import Decimal

from . import issues as _issues
from .money import ZERO, dec, r2
from .spec import get_profile

EXACT, WITHIN_TOLERANCE, BLOCKED = 'exact', 'within_tolerance', 'blocked'
Check = namedtuple('Check', 'rule kind subject odoo pint delta ok')
Reconciliation = namedtuple('Reconciliation', 'state checks issues figures')

_ZERO_TAX_RULE = {
    'Z': 'aligned-ibrp-z-09',
    'E': 'aligned-ibrp-e-09',
    'O': 'aligned-ibrp-o-09',
    'AE': 'aligned-ibrp-ae-09-ae',
    'N': 'ibr-108-ae',
}


def _check(rule, kind, subject, odoo, pint, slack=ZERO):
    delta = dec(odoo) - dec(pint)
    return Check('OFFICIAL:' + rule, kind, subject, dec(odoo), dec(pint), delta, abs(delta) <= slack)


def reconcile(payload, profile=None):
    profile = profile or get_profile()
    items = payload.get('lines', [])
    adjustments = payload.get('adjustments', [])
    totals = payload['odoo_totals']
    checks = []

    # --- lines: ibr-147-ae (exact)
    for line in items:
        base_qty = dec(line.get('base_qty') or 1)
        expected = r2(dec(line['qty']) * dec(line['price_net']) / base_qty)
        checks.append(_check('ibr-147-ae', 'exact', 'line %s' % line['id'], line['net'], expected))

    line_sum = sum((dec(line['net']) for line in items), ZERO)
    allowance_sum = sum((dec(a['amount']) for a in adjustments if not a['charge']), ZERO)
    charge_sum = sum((dec(a['amount']) for a in adjustments if a['charge']), ZERO)
    tax_exclusive = line_sum + charge_sum - allowance_sum

    # --- VAT breakdown per (category, rate)
    groups = {}

    def group(category, rate):
        key = (category, None if rate in (None, '') else dec(rate))
        return groups.setdefault(key, {'taxable': ZERO, 'odoo_tax': ZERO})

    for line in items:
        entry = group(line['category'], line.get('rate'))
        entry['taxable'] += dec(line['net'])
        entry['odoo_tax'] += dec(line.get('odoo_tax'))
    for adj in adjustments:
        entry = group(adj['category'], adj.get('rate'))
        entry['taxable'] += dec(adj['amount']) if adj['charge'] else -dec(adj['amount'])
        entry['odoo_tax'] += dec(adj.get('odoo_tax'))

    # BOOKED TAX: when the caller supplies the tax Odoo actually posted per (category, rate), THAT is the figure (it is
    # the accounting truth and what the XML carries); a per-line estimate would only be a guess about how Odoo rounded.
    booked = payload.get('booked_tax_by_group')
    if booked is not None:
        for key in list(groups):
            groups[key]['odoo_tax'] = ZERO
        for (category, rate), amount in booked.items():
            group(category, rate)['odoo_tax'] = dec(amount)

    breakdown = []
    for (category, rate), entry in sorted(groups.items(), key=lambda kv: (kv[0][0] or '', kv[0][1] or ZERO)):
        subject = 'VAT %s%s' % (category, '' if rate is None else ' %s%%' % rate.normalize())
        if category == 'S':
            checks.append(
                _check('ibr-190-ae', 'exact', subject, rate if rate is not None else ZERO, profile.standard_rate)
            )
            expected = r2(abs(entry['taxable']) * (rate or ZERO) / 100)
            checks.append(
                _check('aligned-ibrp-s-09', 'slack', subject, abs(entry['odoo_tax']), expected, profile.tax_slack)
            )
        else:
            checks.append(
                _check(_ZERO_TAX_RULE.get(category, 'aligned-ibrp-z-09'), 'exact', subject, entry['odoo_tax'], ZERO)
            )
        breakdown.append({'category': category, 'rate': rate, 'taxable': entry['taxable'], 'tax': entry['odoo_tax']})

    breakdown_tax = sum((b['tax'] for b in breakdown), ZERO)

    # --- document totals (exact): the figures are Odoo's
    checks.append(_check('ibr-co-13', 'exact', 'total without VAT (BT-109)', totals['untaxed'], tax_exclusive))
    checks.append(_check('ibr-co-14', 'exact', 'total VAT (BT-110)', totals['tax'], breakdown_tax))
    checks.append(
        _check(
            'ibr-co-15',
            'exact',
            'total with VAT (BT-112)',
            totals['total'],
            dec(totals['untaxed']) + dec(totals['tax']),
        )
    )
    # BT-115 = BT-112 - BT-113 + BT-114. The mapper maps NO prepayment and NO rounding (a cash-rounded document is
    # blocked by the mapper), so for a mapped document this holds by construction; it is kept so a payload that does
    # carry them (a future adapter, the synthetic test payloads) is still checked against the official rule.
    prepaid, rounding = dec(totals.get('prepaid')), dec(totals.get('rounding'))
    payable = dec(totals.get('payable', dec(totals['total']) - prepaid + rounding))
    checks.append(
        _check('ibr-co-16', 'exact', 'amount due (BT-115)', payable, dec(totals['total']) - prepaid + rounding)
    )

    found = []
    for check in checks:
        if not check.ok:
            found.append(
                _issues.make(
                    check.rule,
                    check.subject,
                    "The accounting value %(odoo)s does not satisfy the official rule for %(subject)s (the PINT-AE "
                    "value is "
                    "%(pint)s, difference %(delta)s). The document is blocked and nothing was changed.",
                    {
                        'odoo': str(check.odoo),
                        'pint': str(check.pint),
                        'delta': str(check.delta),
                        'subject': check.subject,
                    },
                )
            )
    if found:
        state = BLOCKED
    elif any(c.delta != 0 for c in checks):
        state = WITHIN_TOLERANCE
    else:
        state = EXACT
    figures = {
        'line_extension': r2(line_sum),
        'allowance_total': r2(allowance_sum) if adjustments else None,
        'charge_total': r2(charge_sum) if adjustments else None,
        'tax_exclusive': r2(totals['untaxed']),
        'tax_total': r2(totals['tax']),
        'tax_inclusive': r2(totals['total']),
        'prepaid': r2(prepaid) if prepaid else None,
        'rounding': r2(rounding) if rounding else None,
        'payable': r2(payable),
        'breakdown': breakdown,
    }
    return Reconciliation(state, checks, found, figures)


def rounding_gap(odoo_tax, taxable, rate):
    """Convenience for analysis: the difference between a booked tax and the official recomputation (never used to
    adjust)."""
    return dec(odoo_tax) - r2(dec(taxable) * dec(rate) / Decimal(100))
