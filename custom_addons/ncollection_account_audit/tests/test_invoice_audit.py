# -*- coding: utf-8 -*-
"""#81 / P8-T05 acceptance: "changing an invoice amount produces an audit entry with old/new values,
user, IP, timestamp".
Permitted changes, rejected changes, audit integrity and accounting regression."""
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon

from odoo.addons.ncollection_account_audit.models import invoice_audit

LOGGER = 'odoo.addons.ncollection_account_audit'


class _FakeRequest:
    """Stands in for ``odoo.http.request`` with a client address."""

    class httprequest:  # noqa: N801 (mirrors the attribute name)
        remote_addr = '203.0.113.9'

    def __bool__(self):
        return True


@tagged('post_install', '-at_install')
class TestInvoiceAmountAudit(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Audit = cls.env['ncollection.invoice.audit']
        cls.manager = cls.env['res.users'].create({
            'name': 'audit_mgr', 'login': 'audit_mgr', 'password': 'Audit-Mgr-Pass1!',
            'group_ids': [Command.set([cls.env.ref('base.group_user').id,
                                       cls.env.ref('account.group_account_manager').id])]})
        cls.plain = cls.env['res.users'].create({
            'name': 'audit_plain', 'login': 'audit_plain', 'password': 'Audit-Plain-Pass1!',
            'group_ids': [Command.set([cls.env.ref('base.group_user').id])]})

    def _invoice(self, amounts=(100.0,), post=False):
        return self.init_invoice('out_invoice', amounts=list(amounts), invoice_date='2026-03-01', post=post)

    def _entries(self, invoice, event=None):
        domain = [('res_id', '=', invoice.id)] + ([('event', '=', event)] if event else [])
        return self.Audit.search(domain, order='id')

    # ------------------------------------------------------------------ permitted changes
    def test_creating_an_invoice_records_one_created_entry_with_its_amounts(self):
        invoice = self._invoice((100.0,))
        rows = self._entries(invoice)
        self.assertEqual(rows.mapped('event'), ['created'])
        row = rows
        self.assertEqual(row.new_amount_total, invoice.amount_total)
        self.assertEqual(row.old_amount_total, 0.0)
        self.assertEqual(row.company_id, invoice.company_id)
        self.assertEqual(row.move_id, invoice)
        self.assertEqual(row.currency_id, invoice.currency_id)

    def test_changing_a_line_amount_records_old_and_new_with_user_and_timestamp(self):
        invoice = self._invoice((100.0,))
        before_total = invoice.amount_total
        started = fields.Datetime.now()
        invoice.invoice_line_ids[0].price_unit = 250.0
        rows = self._entries(invoice, 'amount_changed')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.old_amount_total, before_total)
        self.assertEqual(rows.new_amount_total, invoice.amount_total)
        self.assertNotEqual(rows.old_amount_total, rows.new_amount_total)
        self.assertEqual(rows.new_amount_untaxed, invoice.amount_untaxed)
        self.assertEqual(rows.user_id, self.env.user)
        self.assertEqual(rows.user_login, self.env.user.login)
        self.assertGreaterEqual(rows.logged_at, started)
        self.assertEqual(rows.trigger, 'account.move.line')
        self.assertEqual(rows.company_id, invoice.company_id)
        self.assertEqual(rows.move_name, invoice.name or invoice.display_name)

    def test_the_client_ip_is_recorded_with_a_request_and_empty_without_one(self):
        invoice = self._invoice((100.0,))
        invoice.invoice_line_ids[0].price_unit = 110.0
        self.assertEqual(self._entries(invoice, 'amount_changed').ip_address, '')
        with patch.object(invoice_audit, 'request', _FakeRequest()):
            invoice.invoice_line_ids[0].price_unit = 120.0
        rows = self._entries(invoice, 'amount_changed')
        self.assertEqual(rows[-1].ip_address, '203.0.113.9')
        self.assertEqual(rows[0].ip_address, '')

    def test_a_change_through_the_move_with_line_commands_is_one_entry(self):
        invoice = self._invoice((100.0, 50.0))
        line = invoice.invoice_line_ids[0]
        invoice.write({'invoice_line_ids': [Command.update(line.id, {'price_unit': 300.0, 'quantity': 2}),
                                            Command.create({'name': 'extra', 'price_unit': 10.0, 'quantity': 1,
                                                            'account_id': line.account_id.id})]})
        rows = self._entries(invoice, 'amount_changed')
        self.assertEqual(len(rows), 1, 'one user action is one entry, not one per nested write')
        self.assertEqual(rows.trigger, 'account.move')
        self.assertEqual(rows.new_amount_total, invoice.amount_total)

    def test_two_lines_in_one_write_are_one_entry(self):
        invoice = self._invoice((100.0, 50.0))
        invoice.invoice_line_ids.write({'price_unit': 10.0})
        self.assertEqual(len(self._entries(invoice, 'amount_changed')), 1)

    def test_adding_and_removing_a_line_is_recorded(self):
        invoice = self._invoice((100.0,))
        base = invoice.invoice_line_ids[0]
        new_line = self.env['account.move.line'].create({
            'move_id': invoice.id, 'name': 'added', 'price_unit': 40.0, 'quantity': 1,
            'account_id': base.account_id.id})
        self.assertEqual(len(self._entries(invoice, 'amount_changed')), 1)
        new_line.unlink()
        rows = self._entries(invoice, 'amount_changed')
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1].new_amount_total, rows[0].old_amount_total)

    def test_no_entry_when_the_amounts_do_not_change(self):
        invoice = self._invoice((100.0,))
        count = len(self._entries(invoice))
        invoice.write({'narration': 'a note'})
        invoice.invoice_line_ids[0].name = 'renamed'
        invoice.invoice_line_ids[0].price_unit = invoice.invoice_line_ids[0].price_unit
        invoice.action_post()
        self.assertEqual(len(self._entries(invoice)), count, 'labels, notes, no-ops and posting record nothing')

    def test_deleting_a_draft_invoice_records_its_last_amounts_and_keeps_identity(self):
        invoice = self._invoice((100.0,))
        invoice_id, total, name = invoice.id, invoice.amount_total, invoice.name or invoice.display_name
        invoice.unlink()
        row = self.Audit.search([('res_id', '=', invoice_id), ('event', '=', 'deleted')])
        self.assertEqual(len(row), 1)
        self.assertEqual(row.old_amount_total, total)
        self.assertEqual(row.new_amount_total, 0.0)
        self.assertFalse(row.move_id)
        self.assertEqual(row.move_name, name)
        # the earlier entries survive the invoice
        self.assertTrue(self.Audit.search([('res_id', '=', invoice_id), ('event', '=', 'created')]))

    def test_a_change_after_a_reset_to_draft_is_recorded_on_a_posted_history(self):
        invoice = self._invoice((100.0,), post=True)
        invoice.button_draft()
        invoice.invoice_line_ids[0].price_unit = 400.0
        row = self._entries(invoice, 'amount_changed')
        self.assertEqual(len(row), 1)
        self.assertEqual(row.state, 'draft')
        self.assertEqual(row.new_amount_total, invoice.amount_total)

    def test_non_invoice_entries_are_not_audited(self):
        before = self.Audit.search_count([])
        entry = self.env['account.move'].create({
            'move_type': 'entry', 'journal_id': self.company_data['default_journal_misc'].id,
            'line_ids': [Command.create({'account_id': self.company_data['default_account_receivable'].id,
                                         'debit': 10.0, 'credit': 0.0, 'name': 'd'}),
                         Command.create({'account_id': self.company_data['default_account_revenue'].id,
                                         'debit': 0.0, 'credit': 10.0, 'name': 'c'})]})
        entry.line_ids[0].write({'name': 'x'})
        self.assertEqual(self.Audit.search_count([]), before)

    # ------------------------------------------------------------------ rejected changes
    def test_a_protected_change_on_a_posted_invoice_is_rejected_and_leaves_no_entry(self):
        invoice = self._invoice((100.0,), post=True)
        total = invoice.amount_total
        count = len(self._entries(invoice))
        term = invoice.line_ids.filtered(lambda line: line.display_type == 'payment_term')
        with self.assertRaises(UserError), self.cr.savepoint():
            term.write({'balance': 999.0})
        self.assertEqual(invoice.amount_total, total)
        self.assertEqual(len(self._entries(invoice)), count, 'the rejected change recorded nothing')

    def test_core_lets_a_posted_line_price_change_and_the_audit_flags_it(self):
        """Measured on Odoo 19: writing price_unit on a POSTED invoice line is not refused and does not re-price the
        booked entry, so the booked total stays put while the lines no longer agree with it. That is exactly the
        discrepancy an audit trail exists for: one entry, state posted, booked amounts equal, lines total different."""
        invoice = self._invoice((100.0,), post=True)
        booked = invoice.amount_total
        invoice.invoice_line_ids[0].price_unit = 999.0
        rows = self._entries(invoice, 'amount_changed')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.state, 'posted')
        self.assertEqual((rows.old_amount_total, rows.new_amount_total), (booked, booked))
        self.assertNotEqual(rows.old_lines_total, rows.new_lines_total)

    def test_a_user_without_accounting_rights_is_rejected_and_records_nothing(self):
        invoice = self._invoice((100.0,))
        count = len(self._entries(invoice))
        with self.assertRaises(AccessError), self.cr.savepoint():
            invoice.with_user(self.plain).write({'narration': 'x'})
        with self.assertRaises(AccessError), self.cr.savepoint():
            invoice.invoice_line_ids[0].with_user(self.plain).price_unit = 5.0
        self.assertEqual(len(self._entries(invoice)), count)

    def test_a_permitted_user_is_recorded_as_the_actor(self):
        invoice = self._invoice((100.0,))
        invoice.invoice_line_ids[0].with_user(self.manager).price_unit = 180.0
        row = self._entries(invoice, 'amount_changed')
        self.assertEqual(row.user_id, self.manager)
        self.assertEqual(row.user_login, 'audit_mgr')

    # ------------------------------------------------------------------ audit integrity and access
    def test_entries_cannot_be_edited_or_deleted_even_by_sudo(self):
        invoice = self._invoice((100.0,))
        row = self._entries(invoice)
        for call in (lambda: row.sudo().write({'new_amount_total': 1.0}), lambda: row.sudo().unlink()):
            with self.assertRaises(UserError), self.cr.savepoint():
                call()
        with self.assertRaises(AccessError), self.cr.savepoint():
            self.Audit.with_user(self.manager).create({
                'event': 'created', 'company_id': self.env.company.id, 'logged_at': fields.Datetime.now()})

    def test_a_row_altered_underneath_the_orm_fails_verification(self):
        invoice = self._invoice((100.0,))
        invoice.invoice_line_ids[0].price_unit = 150.0
        rows = self._entries(invoice)
        self.assertEqual(rows.nc_verify(), [])
        self.env.cr.execute("UPDATE ncollection_invoice_audit SET new_amount_total = 1 WHERE id = %s", (rows[-1].id,))
        self.env.invalidate_all()
        self.assertEqual(rows.nc_verify(), [rows[-1].id])

    def test_who_can_read_the_trail(self):
        invoice = self._invoice((100.0,))
        self.assertTrue(self.Audit.with_user(self.manager).search([('res_id', '=', invoice.id)]))
        with self.assertRaises(AccessError):
            self.Audit.with_user(self.plain).search([])

    def test_another_company_cannot_see_the_entries(self):
        invoice = self._invoice((100.0,))
        other = self.env['res.company'].create({'name': 'Audit Other Co'})
        stranger = self.env['res.users'].create({
            'name': 'audit_stranger', 'login': 'audit_stranger', 'password': 'Audit-Str-Pass1!',
            'company_id': other.id, 'company_ids': [Command.set([other.id])],
            'group_ids': [Command.set([self.env.ref('base.group_user').id,
                                       self.env.ref('account.group_account_manager').id])]})
        seen = self.Audit.with_user(stranger).with_company(other).search([('res_id', '=', invoice.id)])
        self.assertFalse(seen)

    def test_the_audit_writes_nothing_to_the_server_log(self):
        with self.assertNoLogs(LOGGER, level='DEBUG'):
            invoice = self._invoice((100.0,))
            invoice.invoice_line_ids[0].price_unit = 77.0
            invoice.unlink()

    # ------------------------------------------------------------------ accounting regression
    def test_posting_payment_and_credit_note_behave_exactly_as_without_the_audit(self):
        invoice = self._invoice((100.0, 20.0), post=True)
        self.assertEqual(invoice.state, 'posted')
        total = invoice.amount_total
        entries = len(self._entries(invoice))
        payment = self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=invoice.ids).create({'amount': total})._create_payments()
        self.assertTrue(payment)
        self.assertEqual(invoice.payment_state in ('paid', 'in_payment'), True)
        self.assertEqual(invoice.amount_total, total, 'payment does not change the amount')
        self.assertEqual(len(self._entries(invoice)), entries, 'a payment is not an amount change')
        reversal = self.env['account.move.reversal'].with_context(
            active_model='account.move', active_ids=invoice.ids).create(
            {'journal_id': invoice.journal_id.id}).refund_moves()
        credit = self.env['account.move'].browse(reversal['res_id'])
        self.assertEqual(credit.amount_total, total)
        self.assertEqual(self._entries(credit).mapped('event'), ['created'])
