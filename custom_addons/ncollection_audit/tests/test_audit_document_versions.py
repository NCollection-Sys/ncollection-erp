# -*- coding: utf-8 -*-
"""Audit Log printable document versions and restore-to-version (19.0.2.2.0).

Every recorded version of a printable document is rendered by the document's OWN report inside a rolled-back savepoint;
the changed sections are marked; a draft document can be restored to a version through the ORM, previewed first,
refused for anything finalised, recorded in the Undo ledger.

Events of one test share one transaction (one ``create_date``), while the version engine orders operations by
transaction time: ``_tick`` gives each step its own time, as separate requests would have."""
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestAuditDocumentVersions(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ncollection.audit.policy']._nc_seed_policies()
        cls.Log = cls.env['auditlog.log'].sudo()
        cls.Doc = cls.env['ncollection.audit.document']
        cls.Service = cls.env['ncollection.audit.service']
        cls.Undo = cls.env['ncollection.audit.undo']
        cls.company = cls.env.company
        cls.journal = cls.env['account.journal'].search(
            [('type', '=', 'sale'), ('company_id', '=', cls.company.id)], limit=1)
        cls.income = cls.env['account.account'].search(
            [('company_ids', 'in', cls.company.id), ('account_type', '=', 'income')], limit=1)
        cls.tax = cls.env['account.tax'].search(
            [('type_tax_use', '=', 'sale'), ('company_id', '=', cls.company.id), ('amount_type', '=', 'percent'),
             ('amount', '>', 0)], limit=1)
        cls.product_a = cls.env['product.product'].create({'name': 'Version Product A', 'list_price': 100})
        cls.product_b = cls.env['product.product'].create({'name': 'Version Product B', 'list_price': 40})
        cls.partner = cls.env['res.partner'].create({'name': 'Version Customer'})
        cls.accountant = new_test_user(
            cls.env, 'au_ver_acc',
            groups='account.group_account_manager,auditlog.group_auditlog_manager,ncollection_audit.group_audit_undo',
            company_id=cls.company.id, company_ids=[(6, 0, cls.company.ids)])
        cls.no_undo = new_test_user(
            cls.env, 'au_ver_noundo', groups='account.group_account_manager,auditlog.group_auditlog_manager',
            company_id=cls.company.id, company_ids=[(6, 0, cls.company.ids)])
        cls.reader_no_accounting = new_test_user(
            cls.env, 'au_ver_reader', groups='base.group_user,auditlog.group_auditlog_user',
            company_id=cls.company.id, company_ids=[(6, 0, cls.company.ids)])

    def setUp(self):
        super().setUp()
        self.assertTrue(self.journal and self.income, "the CI database installs a chart of accounts")
        self._clock = fields.Datetime.now() - timedelta(hours=1)
        self._stamped = 0

    # ---- helpers ---------------------------------------------------------

    def _tick(self):
        """Give every event / amount row written since the last tick its own, later transaction time."""
        self.env.flush_all()
        self._clock += timedelta(seconds=10)
        self.env.cr.execute("UPDATE auditlog_log SET create_date = %s WHERE id > %s", (self._clock, self._stamped))
        self.env.cr.execute("UPDATE ncollection_invoice_audit SET create_date = %s WHERE create_date > %s",
                            (self._clock, self._clock))
        self.env.cr.execute("SELECT COALESCE(MAX(id), 0) FROM auditlog_log")
        self._stamped = self.env.cr.fetchone()[0]
        self.env.invalidate_all()

    def _invoice(self, **line):
        self._tick()
        line = dict({'product_id': self.product_a.id, 'name': 'Line A', 'quantity': 1, 'price_unit': 100,
                     'account_id': self.income.id, 'tax_ids': [Command.clear()]}, **line)
        invoice = self.env['account.move'].with_user(self.accountant).create({
            'move_type': 'out_invoice', 'partner_id': self.partner.id, 'journal_id': self.journal.id,
            'invoice_line_ids': [Command.create(line)]})
        self._tick()
        return invoice

    def _write(self, record, vals):
        record.with_user(self.accountant).write(vals)
        self._tick()

    def _line_logs(self, invoice, method=None):
        domain = [('nc_parent_model', '=', 'account.move'), ('nc_parent_res_id', '=', invoice.id)]
        if method:
            domain.append(('method', '=', method))
        return self.Log.search(domain, order='id')

    def _versions(self, invoice):
        return self.Service.nc_audit_history('account.move', invoice.id)['versions']

    def _snapshot(self, invoice):
        invoice.invalidate_recordset()
        return (invoice.amount_untaxed, invoice.amount_total, invoice.state,
                [(line.name, line.quantity, line.price_unit, line.discount, tuple(line.tax_ids.ids))
                 for line in invoice.invoice_line_ids])

    # ---- printable detection ---------------------------------------------

    def test_printable_detection_uses_the_bound_report_of_the_model(self):
        invoice = self._invoice()
        result = self.Doc.nc_audit_printable('account.move', invoice.id)
        self.assertTrue(result['printable'])
        report = self.Doc._nc_document_report('account.move')
        self.assertEqual(report.report_name, 'account.report_invoice', "the plain invoice, never a variant")
        partner = self.Doc.nc_audit_printable('res.partner', self.partner.id)
        self.assertFalse(partner['printable'], "a contact has no printed document: the data view")
        self.assertTrue(partner['reason'])
        entry = self.env['account.move'].create({
            'move_type': 'entry', 'journal_id': self.env['account.journal'].search(
                [('type', '=', 'general'), ('company_id', '=', self.company.id)], limit=1).id,
            'line_ids': [Command.create({'account_id': self.income.id, 'debit': 5}),
                         Command.create({'account_id': self.income.id, 'credit': 5})]})
        self.assertFalse(self.Doc.nc_audit_printable('account.move', entry.id)['printable'],
                         "a journal entry is not an invoice: no invoice report for it")
        with self.assertRaises(UserError):
            self.Doc.nc_audit_printable('account.move', 'x')

    def test_the_document_frames_never_allow_scripts(self):
        """The server strips scripts, but the boundary is the sandbox: a frame with allow-same-origin AND
        allow-scripts could reach the application. Pinned here so nobody adds it."""
        from odoo.tools.misc import file_open
        with file_open('ncollection_audit/static/src/audit_center/audit_center.xml') as handle:
            source = handle.read()
        frames = source.count('<iframe')
        self.assertGreaterEqual(frames, 4)
        self.assertEqual(source.count('<iframe sandbox="allow-same-origin" '), frames)
        self.assertNotIn('allow-scripts', source)

    # ---- the Changes view ------------------------------------------------

    def test_update_event_renders_the_real_document_with_the_change_marked_and_stores_nothing(self):
        invoice = self._invoice()
        line = invoice.invoice_line_ids
        self._write(line, {'quantity': 3})
        self._write(line, {'discount': 10})
        before_state = self._snapshot(invoice)
        event = self._line_logs(invoice, 'write').filtered(
            lambda log: 'discount' in log.line_ids.mapped('field_name'))[-1:]
        result = self.Doc.nc_audit_document_event(event.id)
        self.assertTrue(result['printable'])
        self.assertEqual(result['action'], 'update')
        self.assertIn('nc-au-chg', result['after'], "the changed section is marked in yellow")
        self.assertGreaterEqual(result['changed'], 1)
        self.assertTrue(result['before'], "an update shows the document before and after")
        self.assertIn('270.00', result['after'].replace(' ', ' '), "after: 3 x 100 less 10%")
        self.assertIn('300.00', result['before'].replace(' ', ' '), "before: 3 x 100, no discount")
        self.assertNotIn('<script', result['after'].lower(), "the frame never carries a script")
        self.assertIn('<base href=', result['after'])
        self.assertEqual(self._snapshot(invoice), before_state, "rendering a version never changes the document")

    def test_create_event_shows_the_document_and_a_line_deletion_shows_what_was_removed(self):
        invoice = self._invoice()
        created = self._line_logs(invoice, 'create')[:1]
        result = self.Doc.nc_audit_document_event(created.id)
        self.assertTrue(result['printable'])
        self.assertIn('Line A', result['after'])
        self._write(invoice, {'invoice_line_ids': [Command.create({
            'product_id': self.product_b.id, 'name': 'Line B gone', 'quantity': 2, 'price_unit': 40,
            'account_id': self.income.id})]})
        added = invoice.invoice_line_ids.filtered(lambda line: line.name == 'Line B gone')
        self._write(invoice, {'invoice_line_ids': [Command.unlink(added.id)]})
        removed = self._line_logs(invoice, 'unlink')
        result = self.Doc.nc_audit_document_event(removed.id)
        self.assertTrue(result['printable'])
        # a deleted line cannot be laid back into the real document: it is listed with its recorded values (the
        # documented limit), never drawn into the report by hand
        self.assertNotIn('Line B gone', result['before'])
        self.assertEqual(len(result['lost_lines']), 1)
        self.assertIn('Line B gone', result['lost_lines'][0])
        self.assertIn('40', result['lost_lines'][0], "the recorded values of the removed line are kept")

    def test_a_document_that_no_longer_exists_is_never_invented(self):
        invoice = self._invoice()
        res_id = invoice.id
        invoice.unlink()
        result = self.Doc.nc_audit_document_compare('account.move', res_id, 1, 2)
        self.assertFalse(result['printable'])
        self.assertTrue(result['reason'])

    # ---- Compare versions ------------------------------------------------

    def test_compare_renders_both_versions_and_marks_the_difference(self):
        invoice = self._invoice()
        line = invoice.invoice_line_ids
        self._write(line, {'price_unit': 150})
        versions = self._versions(invoice)
        self.assertGreaterEqual(len(versions), 2)
        result = self.Doc.nc_audit_document_compare('account.move', invoice.id, versions[-1]['id'],
                                                    versions[0]['id'])
        self.assertTrue(result['printable'])
        self.assertLess(result['version_a'], result['version_b'], "the older version is always A")
        self.assertIn('100.00', result['a'])
        self.assertIn('150.00', result['b'])
        self.assertGreaterEqual(result['changed'], 1)
        self.assertIn('nc-au-chg', result['b'])
        with self.assertRaises(UserError):
            self.Doc.nc_audit_document_compare('account.move', invoice.id, True, versions[0]['id'])
        foreign = self._invoice()
        with self.assertRaises(AccessError):
            self.Doc.nc_audit_document_compare('account.move', invoice.id, self._line_logs(foreign)[0].id,
                                               versions[0]['id'])

    def test_a_reader_without_access_to_the_document_cannot_render_it(self):
        invoice = self._invoice()
        log = self._line_logs(invoice)[:1]
        with self.assertRaises(AccessError):
            self.Doc.with_user(self.reader_no_accounting).nc_audit_document_compare(
                'account.move', invoice.id, log.id, log.id)

    # ---- Restore a version -----------------------------------------------

    def test_restore_a_draft_invoice_to_a_version_lines_and_amounts(self):
        invoice = self._invoice()
        line = invoice.invoice_line_ids
        first = self._line_logs(invoice, 'create')[:1]
        self._write(line, {'quantity': 4, 'discount': 25})
        self._write(invoice, {'invoice_line_ids': [Command.create({
            'product_id': self.product_b.id, 'name': 'Added later', 'quantity': 1, 'price_unit': 40,
            'account_id': self.income.id})]})
        self.assertEqual(invoice.amount_untaxed, 340.0)
        Undo = self.Undo.with_user(self.accountant)
        preview = Undo.nc_restore_preview('account.move', invoice.id, first.id)
        self.assertTrue(preview['available'], preview.get('reason'))
        self.assertTrue(preview['fingerprint'])
        labels = ' '.join(row['label'] for row in preview['rows'])
        self.assertIn('Added later', labels, "a line added after the version is removed")
        self.assertNotIn('writes', preview, "the preview never exposes the write plan")
        result = Undo.nc_restore_apply('account.move', invoice.id, first.id, preview['fingerprint'])
        invoice.invalidate_recordset()
        self.assertEqual(invoice.invoice_line_ids.mapped('name'), ['Line A'])
        self.assertEqual((line.quantity, line.discount), (1, 0))
        self.assertEqual(invoice.amount_untaxed, 100.0, "amounts recomputed by the invoice from its lines")
        ledger = self.Undo.sudo().browse(result['undo_id'])
        self.assertEqual((ledger.kind, ledger.state, ledger.mode), ('version', 'done', 'version'))
        self.assertEqual(ledger.user_id, self.accountant)
        # a version may be restored again later: the one-undo-per-event rule does not apply to versions
        self._tick()
        self._write(line, {'quantity': 2})
        again = Undo.nc_restore_preview('account.move', invoice.id, first.id)
        Undo.nc_restore_apply('account.move', invoice.id, first.id, again['fingerprint'])
        self.assertEqual(line.quantity, 1)

    def test_restore_keeps_the_price_when_only_the_product_is_restored(self):
        invoice = self._invoice()
        line = invoice.invoice_line_ids
        first = self._line_logs(invoice, 'create')[:1]
        self._write(line, {'product_id': self.product_b.id})
        self._write(line, {'price_unit': 77})
        preview = self.Undo.with_user(self.accountant).nc_restore_preview('account.move', invoice.id, first.id)
        self.Undo.with_user(self.accountant).nc_restore_apply('account.move', invoice.id, first.id,
                                                              preview['fingerprint'])
        self.assertEqual(line.product_id, self.product_a)
        self.assertEqual(line.price_unit, 100, "the recorded price of the version, never the product's list price")

    def test_restore_refusals(self):
        invoice = self._invoice()
        line = invoice.invoice_line_ids
        first = self._line_logs(invoice, 'create')[:1]
        self._write(line, {'quantity': 5})
        # not entitled
        refused = self.Undo.with_user(self.no_undo).nc_restore_preview('account.move', invoice.id, first.id)
        self.assertFalse(refused['available'])
        # stale preview
        preview = self.Undo.with_user(self.accountant).nc_restore_preview('account.move', invoice.id, first.id)
        self._write(line, {'quantity': 6})
        with self.assertRaises(UserError):
            self.Undo.with_user(self.accountant).nc_restore_apply('account.move', invoice.id, first.id,
                                                                  preview['fingerprint'])
        self.assertEqual(line.quantity, 6, "nothing written on a refused restore")
        # not entitled to apply: refused before any row is locked, whatever model is named
        with self.assertRaises(UserError):
            self.Undo.with_user(self.no_undo).nc_restore_apply('res.users', self.accountant.id, first.id, 'x' * 64)
        with self.assertRaises(UserError):
            self.Undo.with_user(self.accountant).nc_restore_apply('ncollection.audit.service', 1, first.id, 'x')
        # malformed
        with self.assertRaises(UserError):
            self.Undo.with_user(self.accountant).nc_restore_preview('account.move', str(invoice.id), first.id)
        # posted invoice: refused, the correct workflow named
        invoice.action_post()
        posted = self.Undo.with_user(self.accountant).nc_restore_preview('account.move', invoice.id, first.id)
        self.assertFalse(posted['available'])
        self.assertTrue(posted['hint'] or posted['reason'])
        with self.assertRaises(UserError):
            self.Undo.with_user(self.accountant).nc_restore_apply('account.move', invoice.id, first.id, 'x' * 64)
        self.assertEqual(line.quantity, 6)

    def test_a_deleted_line_is_never_re_added(self):
        invoice = self._invoice()
        self._write(invoice, {'invoice_line_ids': [Command.create({
            'product_id': self.product_b.id, 'name': 'Will be deleted', 'quantity': 1, 'price_unit': 40,
            'account_id': self.income.id})]})
        version = self._line_logs(invoice, 'create')[-1:]
        doomed = invoice.invoice_line_ids.filtered(lambda line: line.name == 'Will be deleted')
        self._write(invoice, {'invoice_line_ids': [Command.unlink(doomed.id)]})
        self._write(invoice.invoice_line_ids, {'quantity': 9})
        preview = self.Undo.with_user(self.accountant).nc_restore_preview('account.move', invoice.id, version.id)
        readd = [row for row in preview['rows'] if row['field'].endswith(':readd')]
        self.assertEqual(len(readd), 1)
        self.assertFalse(readd[0]['restorable'])
        if preview['available']:
            self.Undo.with_user(self.accountant).nc_restore_apply('account.move', invoice.id, version.id,
                                                                  preview['fingerprint'])
        self.assertFalse(invoice.invoice_line_ids.filtered(lambda line: line.name == 'Will be deleted'))
