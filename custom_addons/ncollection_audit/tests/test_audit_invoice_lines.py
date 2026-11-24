# -*- coding: utf-8 -*-
"""Audit Log 2.0 invoice coverage (19.0.2.1.0): invoice lines field by field, amounts from the Invoice Amount Audit in
the same history and comparison, no duplicate amount events, and draft-only line Undo."""
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestAuditInvoiceLines(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ncollection.audit.policy']._nc_seed_policies()
        cls.Log = cls.env['auditlog.log'].sudo()
        cls.Service = cls.env['ncollection.audit.service']
        cls.Undo = cls.env['ncollection.audit.undo']
        cls.company = cls.env.company
        cls.journal = cls.env['account.journal'].search(
            [('type', '=', 'sale'), ('company_id', '=', cls.company.id)], limit=1)
        cls.income = cls.env['account.account'].search(
            [('company_ids', 'in', cls.company.id), ('account_type', '=', 'income')], limit=1)
        cls.tax = cls.env['account.tax'].search(
            [('type_tax_use', '=', 'sale'), ('company_id', '=', cls.company.id), ('amount_type', '=', 'percent')],
            limit=1)
        cls.product_a = cls.env['product.product'].create({'name': 'Audit Line Product A', 'list_price': 100})
        cls.product_b = cls.env['product.product'].create({'name': 'Audit Line Product B', 'list_price': 40})
        cls.partner = cls.env['res.partner'].create({'name': 'Audit Line Customer'})
        cls.accountant = new_test_user(
            cls.env, 'au_inv_acc', groups='account.group_account_manager,ncollection_audit.group_audit_undo',
            company_id=cls.company.id, company_ids=[(6, 0, cls.company.ids)])
        cls.reader_no_accounting = new_test_user(
            cls.env, 'au_inv_reader', groups='base.group_user,auditlog.group_auditlog_user',
            company_id=cls.company.id, company_ids=[(6, 0, cls.company.ids)])

    def setUp(self):
        super().setUp()
        self.assertTrue(self.journal and self.income, "the CI database installs a chart of accounts")

    def _invoice(self, **line):
        line = dict({'product_id': self.product_a.id, 'name': 'Line A', 'quantity': 1, 'price_unit': 100,
                     'account_id': self.income.id, 'tax_ids': [Command.clear()]}, **line)
        return self.env['account.move'].with_user(self.accountant).create({
            'move_type': 'out_invoice', 'partner_id': self.partner.id, 'journal_id': self.journal.id,
            'invoice_line_ids': [Command.create(line)]})

    def _line_logs(self, invoice, method=None):
        domain = [('nc_parent_model', '=', 'account.move'), ('nc_parent_res_id', '=', invoice.id)]
        if method:
            domain.append(('method', '=', method))
        return self.Log.search(domain, order='id')

    # ---- capture ---------------------------------------------------------

    def test_every_line_field_is_recorded_field_by_field_and_linked_to_its_invoice(self):
        invoice = self._invoice()
        line = invoice.invoice_line_ids
        steps = [({'name': 'Line A renamed'}, 'name'), ({'quantity': 3}, 'quantity'),
                 ({'price_unit': 120}, 'price_unit'),
                 ({'discount': 10}, 'discount'), ({'tax_ids': [Command.set(self.tax.ids)]}, 'tax_ids'),
                 ({'product_id': self.product_b.id}, 'product_id')]
        for vals, field in steps:
            before = self._line_logs(invoice).ids
            line.with_user(self.accountant).write(vals)
            self.env.flush_all()
            new = self._line_logs(invoice).filtered(lambda log: log.id not in before)
            self.assertEqual(len(new), 1, "one event per line change: %s" % field)
            self.assertIn(field, new.line_ids.mapped('field_name'))
            self.assertEqual((new.model_model, new.nc_action, new.user_id),
                             ('account.move.line', 'update', self.accountant))
            self.assertEqual(new.nc_company_id, self.company)
        self.assertEqual(set(self.Log.search([('model_model', '=', 'account.move.line'),
                                              ('res_id', '=', line.id)]).line_ids.mapped('field_name'))
                         - {'name', 'quantity', 'price_unit', 'discount', 'tax_ids', 'product_id', 'product_uom_id',
                            'analytic_distribution'}, set(), "only the whitelisted line fields are recorded")

    def test_lines_added_and_removed_are_recorded_and_derived_lines_are_not(self):
        invoice = self._invoice(tax_ids=[Command.set(self.tax.ids)])
        self.assertEqual(len(self._line_logs(invoice, 'create')), 1, "only the product line, never tax / terms lines")
        invoice.with_user(self.accountant).write({'invoice_line_ids': [Command.create({
            'product_id': self.product_b.id, 'name': 'Line B', 'quantity': 2, 'price_unit': 40,
            'account_id': self.income.id})]})
        added = invoice.invoice_line_ids.filtered(lambda line: line.name == 'Line B')
        self.assertTrue(self._line_logs(invoice, 'create').filtered(lambda log: log.res_id == added.id))
        invoice.with_user(self.accountant).write({'invoice_line_ids': [Command.unlink(added.id)]})
        removed = self._line_logs(invoice, 'unlink')
        self.assertEqual(removed.res_id, added.id)
        self.assertIn('quantity', removed.line_ids.mapped('field_name'), "the values before removal are kept")
        entry = self.env['account.move'].create({
            'move_type': 'entry', 'journal_id': self.env['account.journal'].search(
                [('type', '=', 'general'), ('company_id', '=', self.company.id)], limit=1).id,
            'line_ids': [Command.create({'account_id': self.income.id, 'debit': 5}),
                         Command.create({'account_id': self.income.id, 'credit': 5})]})
        self.assertFalse(self._line_logs(entry), "journal entry lines are not invoice lines: never recorded here")

    def test_technical_noise_is_not_recorded(self):
        invoice = self._invoice()
        invoice.with_user(self.accountant).write({'ref': 'NOISE-1'})
        fields_ = self.Log.search([('model_model', '=', 'account.move'), ('res_id', '=', invoice.id),
                                   ('method', '=', 'write')]).line_ids.mapped('field_name')
        self.assertIn('ref', fields_)
        self.assertNotIn('is_manually_modified', fields_)

    # ---- one history, two sources ----------------------------------------

    def test_history_and_compare_include_lines_and_authoritative_amounts(self):
        invoice = self._invoice()
        line = invoice.invoice_line_ids
        line.with_user(self.accountant).write({'quantity': 3})
        line.with_user(self.accountant).write({'price_unit': 120})
        self.env.flush_all()
        logs = self.Service._nc_history_logs('account.move', invoice.id)
        amounts = self.env['ncollection.invoice.audit'].sudo().search([('res_id', '=', invoice.id)], order='id')
        self.assertTrue(amounts, "the Invoice Amount Audit recorded the amount changes")
        # one moment per operation, as separate requests: each amount row a minute after its event
        base = fields.Datetime.now() - timedelta(hours=2)
        moments = {log.id: base + timedelta(minutes=10 * i) for i, log in enumerate(logs)}
        # the invoice and the line created with it are ONE operation (one transaction time), as in real use
        invoice_create = logs.filtered(lambda log: log.model_model == 'account.move' and log.method == 'create')
        for log in logs.filtered(lambda log: log.model_model == 'account.move.line' and log.method == 'create'):
            moments[log.id] = moments[invoice_create.id]
        for log in logs:
            self.env.cr.execute("UPDATE auditlog_log SET create_date = %s WHERE id = %s", (moments[log.id], log.id))
        own = [log for log in logs if log.model_model == 'account.move.line' and log.method == 'write']
        create = logs.filtered(lambda log: log.model_model == 'account.move' and log.method == 'create')[:1]
        for row, event in zip(amounts, [create] + own):
            self.env.cr.execute("UPDATE ncollection_invoice_audit SET create_date = %s WHERE id = %s",
                                (moments[event.id] + timedelta(minutes=1), row.id))
        self.env.invalidate_all()
        history = self.Service.with_user(self.accountant).nc_audit_history('account.move', invoice.id)
        self.assertTrue(any(v['child'] for v in history['versions']), "line events are in the invoice history")
        self.assertEqual(len(history['amounts']), len(amounts), "amount rows are read, not copied")
        ids = [v['id'] for v in history['versions']]
        compare = self.Service.with_user(self.accountant).nc_audit_compare(
            'account.move', invoice.id, create.id, ids[-1])
        rows = {r['field']: r for r in compare['rows']}
        for name in ('amount_untaxed', 'amount_tax', 'amount_total'):
            self.assertIn(name, rows)
        self.assertEqual(rows['amount_total']['status'], 'changed')
        self.assertIn('100', rows['amount_total']['a'])
        self.assertIn('360', rows['amount_total']['b'])
        quantity = next(r for r in compare['rows'] if r['field'].endswith(':quantity'))
        self.assertEqual((quantity['a'], quantity['b'], quantity['status']), ('1.00', '3.00', 'changed'))

    def test_amount_audit_rows_are_not_duplicated_as_audit_events(self):
        rule = self.env['auditlog.rule'].sudo().search(
            [('model_id.model', '=', 'ncollection.invoice.audit')])
        self.assertFalse(rule, "the Invoice Amount Audit is its own trail; its OCA copy rule is withdrawn")
        legacy = self.Log.create({'model_id': self.env['ir.model']._get_id('ncollection.invoice.audit'),
                                  'res_id': 1, 'method': 'create', 'user_id': self.env.uid, 'name': 'legacy copy',
                                  'log_type': 'full'})
        found = self.Service.with_user(self.accountant).nc_audit_search({'reference': 'legacy copy'})
        self.assertNotIn(legacy.id, [r['id'] for r in found['rows']], "old copies are kept but not listed")
        self.assertTrue(legacy.exists(), "history is never deleted")

    def test_line_events_follow_the_invoice_access(self):
        invoice = self._invoice()
        invoice.invoice_line_ids.with_user(self.accountant).write({'quantity': 2})
        log = self._line_logs(invoice, 'write')
        with self.assertRaises(AccessError):
            self.Service.with_user(self.reader_no_accounting).nc_audit_event(log.id)
        action = self.Service.with_user(self.accountant).nc_audit_open_document(log.id)
        self.assertEqual((action['res_model'], action['res_id']), ('account.move', invoice.id),
                         "a line event opens its invoice")
        event = self.Service.with_user(self.accountant).nc_audit_event(log.id)
        self.assertEqual((event['history_model'], event['history_res_id']), ('account.move', invoice.id))

    # ---- line Undo -------------------------------------------------------

    def test_draft_line_change_is_restored_and_posted_is_refused(self):
        invoice = self._invoice()
        line = invoice.invoice_line_ids
        line.with_user(self.accountant).write({'quantity': 4})
        log = self._line_logs(invoice, 'write')
        Undo = self.Undo.with_user(self.accountant)
        preview = Undo.nc_undo_preview(log.id)
        self.assertTrue(preview['available'], preview.get('reason'))
        Undo.nc_undo_apply(log.id, ['quantity'], preview['fingerprint'])
        self.assertEqual(line.quantity, 1.0)
        line.with_user(self.accountant).write({'price_unit': 150})
        later = self._line_logs(invoice, 'write')[-1:]
        invoice.action_post()
        refused = Undo.nc_undo_preview(later.id)
        self.assertFalse(refused['available'], "a posted invoice's line is never rewritten")
        self.assertTrue(refused['hint'])

    def test_removed_line_is_never_re_added_and_added_line_removal_is_draft_only(self):
        invoice = self._invoice()
        invoice.with_user(self.accountant).write({'invoice_line_ids': [Command.create({
            'product_id': self.product_b.id, 'name': 'Extra', 'quantity': 1, 'price_unit': 40,
            'account_id': self.income.id})]})
        extra = invoice.invoice_line_ids.filtered(lambda line: line.name == 'Extra')
        added = self._line_logs(invoice, 'create').filtered(lambda log: log.res_id == extra.id)
        Undo = self.Undo.with_user(self.accountant)
        preview = Undo.nc_undo_preview(added.id)
        self.assertTrue(preview['available'], preview.get('reason'))
        Undo.nc_undo_apply(added.id, [], preview['fingerprint'])
        self.assertFalse(extra.exists(), "undoing an added line on a draft removes it")
        removed = self._line_logs(invoice, 'unlink')
        self.assertFalse(Undo.nc_undo_preview(removed.id)['available'], "a removed line is never re-added")

    def test_locked_period_refuses_line_undo(self):
        invoice = self._invoice()
        invoice.write({'invoice_date': fields.Date.to_date('2021-01-15'), 'date': fields.Date.to_date('2021-01-15')})
        invoice.invoice_line_ids.with_user(self.accountant).write({'quantity': 5})
        log = self._line_logs(invoice, 'write')
        self.company.sudo().fiscalyear_lock_date = fields.Date.to_date('2021-01-31')
        self.assertFalse(self.Undo.with_user(self.accountant).nc_undo_preview(log.id)['available'])

    def test_restoring_only_the_product_never_re_prices_the_line(self):
        invoice = self._invoice()
        line = invoice.invoice_line_ids
        line.with_user(self.accountant).write({'product_id': self.product_b.id})
        product_event = self._line_logs(invoice, 'write')[-1:]
        line.with_user(self.accountant).write({'price_unit': 77.0, 'tax_ids': [Command.set(self.tax.ids)]})
        price, taxes, label, account = line.price_unit, line.tax_ids, line.name, line.account_id
        Undo = self.Undo.with_user(self.accountant)
        preview = Undo.nc_undo_preview(product_event.id)
        self.assertTrue(preview['available'], preview.get('reason'))
        self.assertTrue(preview['related'], "the preview says what is kept")
        Undo.nc_undo_apply(product_event.id, ['product_id'], preview['fingerprint'])
        self.assertEqual(line.product_id, self.product_a)
        self.assertEqual((line.price_unit, line.tax_ids, line.name, line.account_id),
                         (price, taxes, label, account),
                         "only the product changed: price, taxes and label are what the user saw")

    def test_create_returns_every_record_whatever_the_audit_records(self):
        """The capture filters what it RECORDS, never what create() returns: journal-entry, tax and rounding lines
        are created by Odoo and its callers through the return value (security review C1)."""
        MoveLine = self.env['account.move.line']
        entry = self.env['account.move'].create({
            'move_type': 'entry', 'journal_id': self.env['account.journal'].search(
                [('type', '=', 'general'), ('company_id', '=', self.company.id)], limit=1).id})
        vals = [{'move_id': entry.id, 'account_id': self.income.id, 'debit': 10, 'name': 'd'},
                {'move_id': entry.id, 'account_id': self.income.id, 'credit': 10, 'name': 'c'}]
        created = MoveLine.with_context(check_move_validity=False).create(vals)
        self.assertEqual(len(created), 2, "journal-entry lines are returned even though they are not recorded")
        invoice = self._invoice(tax_ids=[Command.set(self.tax.ids)])
        self.assertTrue(invoice.line_ids.filtered(lambda line: line.display_type == 'tax'),
                        "the invoice's derived tax line exists")
        self.assertEqual(len(self._line_logs(invoice, 'create')), 1, "only its product line is recorded")

    def test_a_line_added_and_removed_later_does_not_exist_in_an_earlier_version(self):
        invoice = self._invoice()
        invoice.with_user(self.accountant).write({'ref': 'V2'})
        invoice.with_user(self.accountant).write({'invoice_line_ids': [Command.create({
            'product_id': self.product_b.id, 'name': 'Temporary', 'quantity': 1, 'price_unit': 40,
            'account_id': self.income.id})]})
        temporary = invoice.invoice_line_ids.filtered(lambda line: line.name == 'Temporary')
        invoice.with_user(self.accountant).write({'invoice_line_ids': [Command.unlink(temporary.id)]})
        logs = self.Service._nc_history_logs('account.move', invoice.id)
        first = logs.filtered(lambda log: log.model_model == 'account.move' and log.method == 'create')
        compare = self.Service.with_user(self.accountant).nc_audit_compare(
            'account.move', invoice.id, first.id, logs[-1].id)
        temp_rows = [r for r in compare['rows'] if r['field'].startswith('account.move.line:%s:' % temporary.id)]
        self.assertTrue(temp_rows)
        self.assertTrue(all(r['a_kind'] == 'empty' and r['b_kind'] == 'empty' for r in temp_rows),
                        "absent before it was added and after it was removed")
