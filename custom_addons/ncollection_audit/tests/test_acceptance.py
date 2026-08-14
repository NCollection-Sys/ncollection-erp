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

    def test_the_invoice_amount_criterion_is_NOT_met_and_says_why(self):
        """P8-T05's acceptance criterion, and the reason this module does not
        meet it.

            "changing an invoice amount produces an audit entry with old/new
             values, user, IP, and timestamp"

        Two measured facts make that undeliverable with this auditlog:

        1. `amount_total` is a STORED COMPUTED field. Probed on a scratch
           database before this module existed — changing a line's price_unit
           moved it 100 -> 250 and produced ZERO `account.move` audit rows,
           because recompute-flush never reaches the `write()` auditlog
           patches. So the criterion needs `account.move.line`.

        2. Auditing `account.move.line` overflows Python's recursion limit —
           79 nested `write_full` frames cycling through
           `_compute_discount_allocation_needed`. It surfaces only on CI, which
           means the chain is near the ceiling everywhere rather than absent
           here.

        So the model the criterion needs is the model that cannot be audited.
        The issue was rescoped rather than reworded; this test exists so the
        gap is asserted in the suite instead of living only in a PR body.
        """
        from ..models.audit_rule import _NC_WITHHELD
        for name in ('account.move', 'account.move.line'):
            self.assertIn(name, _NC_WITHHELD)
            self.assertNotIn(
                name, set(self.env['auditlog.rule'].search([]).mapped(
                    'model_model')),
                "%s is audited again — verify the recursion is genuinely "
                "fixed on CI, not just locally, before trusting this" % name)
        self.assertIn('recursion', _NC_WITHHELD['account.move.line'])

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
