# -*- coding: utf-8 -*-
"""Issuance deadline: 14 Business Days from the Date of Business Transaction (MD 243/2025 Art 6(5) as replaced by MD
197/2026 Art 2; "Business Day" as defined in MD 243 Art 1: not a weekend day, not an official holiday).

Expected dates were computed independently of the implementation and checked by hand for the first case."""

from datetime import date
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from ..lib import business_days
from .common import EInvoiceCase


@tagged('post_install', '-at_install')
class TestBusinessDays(TransactionCase):

    def test_fourteen_business_days_from_a_wednesday(self):
        # Thu 29, Fri 30, Mon 2 ... Tue 17 November: the 14th Business Day
        self.assertEqual(business_days.issuance_deadline(date(2026, 10, 28)), date(2026, 11, 17))

    def test_a_weekend_never_counts(self):
        self.assertFalse(business_days.is_business_day(date(2026, 10, 31)))  # Saturday
        self.assertFalse(business_days.is_business_day(date(2026, 11, 1)))  # Sunday
        self.assertTrue(business_days.is_business_day(date(2026, 10, 30)))  # Friday is a working day

    def test_a_friday_and_the_following_saturday_reach_the_same_deadline(self):
        self.assertEqual(business_days.issuance_deadline(date(2026, 10, 30)), date(2026, 11, 19))
        self.assertEqual(business_days.issuance_deadline(date(2026, 10, 31)), date(2026, 11, 19))

    def test_holidays_push_the_deadline(self):
        holidays = {date(2026, 12, 2), date(2026, 12, 3)}
        self.assertEqual(business_days.issuance_deadline(date(2026, 11, 30)), date(2026, 12, 18))
        self.assertEqual(business_days.issuance_deadline(date(2026, 11, 30), holidays), date(2026, 12, 22))

    def test_a_holiday_on_a_weekend_changes_nothing(self):
        self.assertEqual(
            business_days.issuance_deadline(date(2026, 10, 28), {date(2026, 10, 31)}), date(2026, 11, 17)
        )

    def test_year_end_boundary(self):
        self.assertEqual(business_days.issuance_deadline(date(2026, 12, 24)), date(2027, 1, 13))

    def test_month_end_boundary(self):
        self.assertEqual(business_days.issuance_deadline(date(2027, 1, 29)), date(2027, 2, 18))

    def test_leap_year_february(self):
        self.assertEqual(business_days.issuance_deadline(date(2028, 2, 14)), date(2028, 3, 3))

    def test_the_weekend_is_a_parameter(self):
        # the next Business Day after Thursday 29 Oct: Friday under Sat-Sun, Sunday under a Fri-Sat weekend
        self.assertEqual(business_days.add_business_days(date(2026, 10, 29), 1), date(2026, 10, 30))
        self.assertEqual(business_days.add_business_days(date(2026, 10, 29), 1, weekend=(4, 5)), date(2026, 11, 1))

    def test_no_date_no_deadline(self):
        self.assertIsNone(business_days.issuance_deadline(None))

    def test_bad_input_is_refused(self):
        with self.assertRaises(ValueError):
            business_days.add_business_days(date(2026, 10, 28), -1)
        with self.assertRaises(TypeError):
            business_days.add_business_days('2026-10-28', 1)


@tagged('post_install', '-at_install')
class TestDeadlineOnRealDocuments(EInvoiceCase):

    def test_the_deadline_is_stored_at_preparation_from_the_invoice_date(self):
        move = self._invoice(invoice_date=date(2026, 10, 28), invoice_date_due=date(2026, 11, 28))
        record = self._einvoice(move)
        record.action_prepare()
        self.assertEqual(record.transaction_date, date(2026, 10, 28))
        self.assertEqual(record.issue_deadline, date(2026, 11, 17))
        self.assertFalse(record.vat_issue_deadline, "the VAT-Law rule is not configured: nothing is assumed")

    def test_the_holiday_calendar_is_used(self):
        self.env['ncollection.einvoice.holiday'].create({'date': date(2026, 12, 2), 'name': 'National Day'})
        self.env['ncollection.einvoice.holiday'].create(
            {'date': date(2026, 12, 3), 'name': 'National Day', 'company_id': self.company.id}
        )
        move = self._invoice(invoice_date=date(2026, 11, 30), invoice_date_due=date(2026, 12, 30))
        record = self._einvoice(move)
        record.action_prepare()
        self.assertEqual(record.issue_deadline, date(2026, 12, 22))

    def test_the_vat_law_rule_is_separate_and_configured(self):
        self.config.vat_issue_days = 14
        move = self._invoice(invoice_date=date(2026, 10, 28), invoice_date_due=date(2026, 11, 28))
        record = self._einvoice(move)
        record.action_prepare()
        self.assertEqual(record.vat_issue_deadline, date(2026, 11, 11))
        self.assertEqual(record.issue_deadline, date(2026, 11, 17))

    def test_overdue_is_judged_in_the_uae_time_zone_and_is_searchable(self):
        move = self._invoice(invoice_date=date(2026, 10, 28), invoice_date_due=date(2026, 11, 28))
        record = self._einvoice(move)
        record.action_prepare()
        model = type(record)
        with patch.object(model, '_nc_today', lambda self: date(2026, 11, 17)):
            record.invalidate_recordset(['is_overdue'])
            self.assertFalse(record.is_overdue, "the deadline day itself is not overdue")
        with patch.object(model, '_nc_today', lambda self: date(2026, 11, 18)):
            record.invalidate_recordset(['is_overdue'])
            self.assertTrue(record.is_overdue)
            self.assertIn(record, self.env['ncollection.einvoice'].search([('is_overdue', '=', True)]))
            self.assertNotIn(record, self.env['ncollection.einvoice'].search([('is_overdue', '=', False)]))

    def test_a_not_required_document_has_no_deadline(self):
        consumer = self._partner('Private Person', state='AZ')
        consumer.nc_einvoice_party_type = 'consumer'
        record = self._einvoice(self._invoice(partner=consumer))
        record.action_prepare()
        self.assertEqual(record.local_state, 'not_required')
        self.assertFalse(record.issue_deadline)
        self.assertFalse(record.is_overdue)
