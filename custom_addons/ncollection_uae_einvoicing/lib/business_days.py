# -*- coding: utf-8 -*-
"""Business-day arithmetic for the issuance deadline (pure: dates in, dates out; no time zones inside).

MD 243/2025 Art 6(5) as replaced by MD 197/2026 Art 2: an Electronic Invoice or Electronic Credit Note must be issued and
transmitted "within fourteen (14) Business Days from the Date of Business Transaction". MD 243 Art 1 defines a Business
Day as any day of the week except weekends and official holidays of the Federal Government.

* The weekend is Saturday and Sunday (the UAE Federal Government working week since 1 January 2022); it is a parameter
  here, not a hidden constant, so a future change is configuration.
* Official holidays are NOT in this file: they are announced each year (several move with the lunar calendar), so they
  come from the configurable ``ncollection.einvoice.holiday`` calendar and are passed in.
* Counting: the Date of Business Transaction itself is day 0; the deadline is the 14th Business Day after it. A
  transaction dated on a weekend or holiday starts counting from the next Business Day all the same.

Callers pass plain ``datetime.date`` values already resolved in the UAE time zone (Asia/Dubai), so no instant is ever
interpreted here.
"""

from datetime import date, timedelta

ISSUANCE_BUSINESS_DAYS = 14
DEFAULT_WEEKEND = (5, 6)  # Saturday, Sunday (date.weekday())


def is_business_day(day, holidays=(), weekend=DEFAULT_WEEKEND):
    return day.weekday() not in weekend and day not in holidays


def add_business_days(start, count, holidays=(), weekend=DEFAULT_WEEKEND):
    """The ``count``-th Business Day strictly after ``start``."""
    if not isinstance(start, date):
        raise TypeError('start must be a date')
    if count < 0:
        raise ValueError('count must be >= 0')
    holidays = frozenset(holidays)
    day, remaining = start, count
    while remaining:
        day += timedelta(days=1)
        if is_business_day(day, holidays, weekend):
            remaining -= 1
    return day


def issuance_deadline(transaction_date, holidays=(), weekend=DEFAULT_WEEKEND):
    """The last day to issue and transmit under MD 243 Art 6(5) (as replaced by MD 197), or None without a date."""
    if not isinstance(transaction_date, date):
        return None
    return add_business_days(transaction_date, ISSUANCE_BUSINESS_DAYS, holidays, weekend)
