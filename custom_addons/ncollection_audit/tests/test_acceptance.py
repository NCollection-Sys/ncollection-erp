# -*- coding: utf-8 -*-
"""P8-T05, the acceptance criterion, taken literally.

    "changing an invoice amount produces an audit entry with old/new values,
     user, IP, and timestamp"

Five fields. `auditlog` supplies four; the fifth is this module's.

THE REASON THIS FILE EXISTS AT ALL is that the obvious version of this test
passes against a broken configuration. Seed a rule on `account.move`, change
`move.ref`, see a log, conclude that amounts are covered — and be wrong.
Measured on a scratch database before a line of this module was written:

    PROBE amount_total before: 100.0
    PROBE amount_total after : 250.0
    PROBE move logs before/after: 4 4

`amount_total` is a stored computed field. Editing a line moves it through
recompute-flush, which never passes through the `write()` that `auditlog`
patches, so the move-level rule sees nothing. The test below therefore changes
the amount **the way a user does** — by editing the line — and asserts an entry
appears, which is only true because `account.move.line` is seeded too.
"""
from unittest.mock import patch

from odoo.tests import tagged

from odoo.addons.ncollection_audit.models import auditlog_log as nc_log

from .common import AuditCommon


@tagged('post_install', '-at_install')
class TestAcceptance(AuditCommon):

    def _require_account(self):
        """Skip ONLY where accounting genuinely is not installed.

        This module depends on `auditlog` alone on purpose — it installs on the
        platform database too, which has no `account` (verified: `saastest`
        reports account `uninstalled`). In CI `account` IS installed, so this
        never skips there — and if it ever did, `scripts/ci/check_skips.py`
        fails the build on the unexpected skip rather than counting it as a
        pass (#363). The guard does the work; this is not an escape hatch.
        """
        if 'account.move' not in self.env:
            self.skipTest("account is not installed on this database")

    def test_changing_an_invoice_amount_produces_a_full_audit_entry(self):
        """THE acceptance criterion, end to end."""
        self._require_account()
        move = self._invoice(price_unit=100.0)
        self.env['auditlog.log'].search([]).unlink()   # start from a clean slate

        move.invoice_line_ids[0].price_unit = 250.0
        self.env.flush_all()

        line_model = self.env['ir.model']._get('account.move.line')
        logs = self.env['auditlog.log'].search(
            [('model_id', '=', line_model.id), ('method', '=', 'write')])
        self.assertTrue(
            logs,
            "changing the invoice amount produced NO audit entry. A rule on "
            "account.move alone cannot see this: amount_total is a stored "
            "computed field written by recompute-flush, which never reaches "
            "the write() auditlog patches.")

        changed = {line.field_name: (line.old_value, line.new_value)
                   for log in logs for line in log.line_ids}
        self.assertIn('price_unit', changed, "the changed field is not logged")
        old_value, new_value = changed['price_unit']
        self.assertEqual((old_value, new_value), ('100.0', '250.0'),
                         "old/new values are wrong — under log_type='fast' the "
                         "old value would be a hardcoded False")

        entry = logs[0]
        self.assertEqual(entry.user_id, self.env.user, "no acting user")
        self.assertTrue(entry.create_date, "no timestamp")
        # IP is a separate assertion below: a test writes with no HTTP request,
        # so empty here is the CORRECT answer, not a missing feature.
        self.assertEqual(entry.nc_remote_addr, '',
                         "a write with no HTTP request must record no IP "
                         "rather than inventing one")

    def test_the_ip_is_captured_when_there_IS_a_request(self):
        """The fifth field. `auditlog` has no IP anywhere — grepped for
        remote_addr/ip_address across the module: zero hits."""
        class FakeHttpRequest:
            remote_addr = '203.0.113.9'

        class FakeRequest:
            httprequest = FakeHttpRequest()

            def __bool__(self):
                return True

        with patch.object(nc_log, 'request', FakeRequest()):
            self._noise('IP Probe')
        log = self._latest_log('res.country')
        self.assertEqual(log.nc_remote_addr, '203.0.113.9')

    def test_a_write_with_no_request_records_no_ip_and_does_not_raise(self):
        """Crons, queue jobs and the shell all write with no request —
        measured: `PROBE http request during shell write: False`. An audit row
        must never fail to be written because its metadata was unavailable."""
        with patch.object(nc_log, 'request', None):
            self._noise('No Request')
        self.assertEqual(self._latest_log('res.country').nc_remote_addr, '')

    def test_every_seeded_rule_logs_in_FULL_mode(self):
        """`write_fast` sets `old_vals2 = dict.fromkeys(vals2.keys(), False)` —
        under 'fast' every old value is a hardcoded False, so the acceptance
        criterion's "old/new values" would be satisfied in shape and false in
        substance. Verified in the vendored source, and pinned here."""
        rules = self.env['auditlog.rule'].search([])
        self.assertTrue(rules, "no rules seeded; this proves nothing")
        self.assertEqual(
            set(rules.mapped('log_type')), {'full'},
            "a seeded rule uses 'fast', which fabricates old values as False")
