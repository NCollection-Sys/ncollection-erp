# -*- coding: utf-8 -*-
"""Stamps on the accounting documents: which kind each one is, and that the PDF Odoo really prints carries it.

Synthetic data only. The selection tests need no PDF; ONE test drives the real wkhtmltopdf render (``force_report_
rendering``) to prove the hook stamps the document Odoo actually produces, and reads the position back out of it.
"""
import ast
import base64
import io

from PIL import Image

from odoo import Command
from odoo.exceptions import AccessError
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.ncollection_branding.tests import pdf_probe
from odoo.tests import HttpCase, tagged


def _added(stamped, bare):
    """What the stamped PDF draws beyond the bare render of the same document, page by page: the stamps (a real
    document also draws its own images, such as the company logo, with the same operator)."""
    result = []
    for after, before in zip(pdf_probe.placements(stamped), pdf_probe.placements(bare)):
        known = [tuple(round(v, 1) for v in found) for found in before]
        result.append([found for found in after if tuple(round(v, 1) for v in found) not in known])
    return result


def _png(color=(160, 20, 20, 150)):
    buffer = io.BytesIO()
    Image.new('RGBA', (80, 40), color).save(buffer, 'PNG')
    return base64.b64encode(buffer.getvalue())


class _Common(AccountTestInvoicingCommon):

    def _type(self, code):
        return self.env.ref('ncollection_account_core.doc_type_%s' % code)

    def _stamp(self, codes, company=None, **values):
        types = self.env['ncollection.document.type']
        for code in ([codes] if isinstance(codes, str) else codes):
            types |= self._type(code)
        vals = {'name': 'Stamp ' + ','.join(types.mapped('code')), 'company_id': (company or self.env.company).id,
                'image': _png(), 'document_type_ids': [Command.set(types.ids)]}
        vals.update(values)
        return self.env['ncollection.document.stamp'].create(vals)

    def _winner(self, report_xmlid, record):
        return self.env['ncollection.document.stamp']._nc_winner(self.env.ref(report_xmlid), record)


@tagged('post_install', '-at_install')
class TestAccountingDocumentKinds(_Common):

    def test_the_document_types_are_registered_with_the_reports_that_print_them(self):
        invoices = self.env.ref('account.account_invoices')
        for code in ('customer_invoice', 'customer_credit_note', 'vendor_bill', 'vendor_credit_note'):
            self.assertIn(invoices, self._type(code).report_ids, code)
        receipt_report = self.env.ref('account.action_report_payment_receipt')
        self.assertEqual(self._type('payment_receipt').report_ids, receipt_report)

    def test_an_invoice_a_credit_note_a_bill_and_a_vendor_credit_are_four_different_kinds(self):
        invoice = self.init_invoice('out_invoice', post=True, amounts=[100.0], invoice_date='2026-03-01')
        credit = self.init_invoice('out_refund', post=True, amounts=[10.0], invoice_date='2026-03-01')
        bill = self.init_invoice('in_invoice', post=True, amounts=[100.0], invoice_date='2026-03-01')
        vendor_credit = self.init_invoice('in_refund', post=True, amounts=[10.0], invoice_date='2026-03-01')
        official = self._stamp('customer_invoice')
        only_credit = self._stamp('customer_credit_note')
        purchase = self._stamp(['vendor_bill', 'vendor_credit_note'])
        xmlid = 'account.account_invoices'
        self.assertEqual(self._winner(xmlid, invoice), official)
        self.assertEqual(self._winner(xmlid, credit), only_credit)
        self.assertEqual(self._winner(xmlid, bill), purchase)
        self.assertEqual(self._winner(xmlid, vendor_credit), purchase)
        # the other invoice report prints the same kinds, so the same stamps
        self.assertEqual(self._winner('account.account_invoices_without_payment', invoice), official)

    def test_a_kind_without_a_stamp_is_simply_not_stamped(self):
        invoice = self.init_invoice('out_invoice', post=True, amounts=[100.0], invoice_date='2026-03-01')
        bill = self.init_invoice('in_invoice', post=True, amounts=[100.0], invoice_date='2026-03-01')
        self._stamp('customer_invoice')
        self.assertTrue(self._winner('account.account_invoices', invoice))
        self.assertFalse(self._winner('account.account_invoices', bill), "an invoice stamp never lands on a bill")

    def test_a_payment_receipt_has_its_own_stamp(self):
        invoice = self.init_invoice('out_invoice', post=True, amounts=[100.0], invoice_date='2026-03-01')
        payment = self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=invoice.ids).create({})._create_payments()
        paid = self._stamp('payment_receipt', name='Paid')
        self._stamp('customer_invoice')
        self.assertEqual(self._winner('account.action_report_payment_receipt', payment), paid)
        self.assertFalse(self._winner('account.account_invoices', payment.move_id),
                         "the receipt's own journal entry is not an invoice")

    def test_the_stamp_of_another_company_is_never_used(self):
        other = self.setup_other_company()['company']
        invoice = self.init_invoice('out_invoice', post=True, amounts=[100.0], invoice_date='2026-03-01')
        self._stamp('customer_invoice', company=other)
        self.assertFalse(self._winner('account.account_invoices', invoice))
        mine = self._stamp('customer_invoice')
        self.assertEqual(self._winner('account.account_invoices', invoice), mine)


@tagged('post_install', '-at_install')
class TestStampOnTheRealPdf(HttpCase, _Common):
    """The whole path: Odoo's own wkhtmltopdf render, the per-record hook, the stamp on the bytes it returns."""

    def test_the_pdf_odoo_prints_carries_the_stamp_at_the_template_millimetres(self):
        invoice = self.init_invoice('out_invoice', post=True, amounts=[100.0], invoice_date='2026-03-01')
        credit = self.init_invoice('out_refund', post=True, amounts=[10.0], invoice_date='2026-03-01')
        self._stamp('customer_invoice', x_mm=120.0, y_mm=235.0, width_mm=40.0, height_mm=20.0, page_scope='last')
        report = invoice._nc_print_report().with_context(force_report_rendering=True)
        printed, kind = report._render_qweb_pdf(report.report_name, invoice.ids)
        self.assertEqual(kind, 'pdf')
        self.assertTrue(printed.startswith(b'%PDF'))
        bare, _kind = report.sudo().with_context(nc_no_stamp=True)._render_qweb_pdf(report.report_name, invoice.ids)
        pages = _added(printed, bare)
        self.assertEqual([len(found) for found in pages], [0] * (len(pages) - 1) + [1],
                         'stamped once, on the last page')
        for got, want in zip(pages[-1][0], (120.0, 235.0, 40.0, 20.0)):
            self.assertAlmostEqual(got, want, places=1)
        # a credit note is another kind: the same engine, the same report, no stamp
        unstamped, _kind = report._render_qweb_pdf(report.report_name, credit.ids)
        credit_bare, _kind = report.sudo().with_context(nc_no_stamp=True)._render_qweb_pdf(
            report.report_name, credit.ids)
        self.assertEqual(sum(len(found) for found in _added(unstamped, credit_bare)), 0)

    def test_two_documents_of_two_companies_in_one_print_are_each_stamped_with_their_own_stamp(self):
        other = self.setup_other_company()['company']
        mine = self.init_invoice('out_invoice', post=True, amounts=[100.0], invoice_date='2026-03-01')
        theirs = self.init_invoice('out_invoice', post=True, amounts=[100.0], invoice_date='2026-03-01', company=other)
        self.env.user.company_ids |= other
        self._stamp('customer_invoice', x_mm=20.0, y_mm=20.0, width_mm=20.0, height_mm=10.0, page_scope='first')
        self._stamp('customer_invoice', company=other, x_mm=150.0, y_mm=20.0, width_mm=20.0, height_mm=10.0,
                    page_scope='first')
        report = mine._nc_print_report().with_context(force_report_rendering=True, allowed_company_ids=[
            self.env.company.id, other.id])
        printed, _kind = report._render_qweb_pdf(report.report_name, (mine | theirs).ids)
        bare, _kind = report.sudo().with_context(nc_no_stamp=True)._render_qweb_pdf(
            report.report_name, (mine | theirs).ids)
        xs = sorted(round(found[0][0]) for found in _added(printed, bare) if found)
        self.assertEqual(xs, [20, 150], "each invoice carries its own company's stamp, and only that one")


@tagged('post_install', '-at_install')
class TestAccountingStampEntry(_Common):
    """Accounting > Configuration > Document Stamps: the same model, designer and overlay, reachable by the adviser."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        base = cls.env.ref('base.group_user')
        cls.adviser = Users.create({
            'name': 'Stamp Adviser', 'login': 'stamp_adviser', 'company_id': cls.env.company.id,
            'company_ids': [Command.set(cls.env.company.ids)],
            'group_ids': [Command.set([base.id, cls.env.ref('account.group_account_manager').id])]})
        cls.accountant = Users.create({
            'name': 'Stamp Accountant', 'login': 'stamp_accountant', 'company_id': cls.env.company.id,
            'company_ids': [Command.set(cls.env.company.ids)],
            'group_ids': [Command.set([base.id, cls.env.ref('account.group_account_user').id])]})

    def test_the_entry_sits_under_accounting_configuration_for_the_adviser_only(self):
        menu = self.env.ref('ncollection_account_core.menu_accounting_document_stamps')
        self.assertEqual(menu.parent_id, self.env.ref('account.menu_finance_configuration'))
        self.assertEqual(menu.action.res_model, 'ncollection.document.stamp', 'the one stamp model, no second system')
        self.assertIn(self.env.ref('account.group_account_manager'), menu.group_ids)
        visible = self.env['ir.ui.menu'].with_user(self.adviser)._visible_menu_ids()
        self.assertIn(menu.id, visible)
        self.assertNotIn(menu.id, self.env['ir.ui.menu'].with_user(self.accountant)._visible_menu_ids())

    def test_the_entry_lists_and_offers_only_the_accounting_kinds(self):
        self._stamp('customer_invoice', name='Accounting seal')
        Kind = self.env['ncollection.document.type'].sudo()
        company_kind = Kind.create({'name': 'Other kind', 'code': 'other_kind'})
        self.env['ncollection.document.stamp'].create({
            'name': 'Not accounting', 'company_id': self.env.company.id, 'image': _png(),
            'document_type_ids': [Command.set(company_kind.ids)]})
        action = self.env.ref('ncollection_account_core.action_accounting_document_stamp')
        listed = self.env['ncollection.document.stamp'].search(
            ast.literal_eval(action.domain)).mapped('name')
        self.assertIn('Accounting seal', listed)
        self.assertNotIn('Not accounting', listed)
        form = self.env.ref('ncollection_account_core.view_document_stamp_form_accounting')
        self.assertIn('payment_receipt', form.arch)

    def test_an_adviser_configures_the_stamp_and_print_and_view_use_it(self):
        invoice = self.init_invoice('out_invoice', post=True, amounts=[100.0], invoice_date='2026-03-01')
        stamp = self.env['ncollection.document.stamp'].with_user(self.adviser).create({
            'name': 'By the adviser', 'company_id': self.env.company.id, 'image': _png(),
            'document_type_ids': [Command.set(self._type('customer_invoice').ids)]})
        # the very function Print's per-record hook and the View route both resolve the stamp with
        self.assertEqual(self._winner('account.account_invoices', invoice), stamp)
        self.assertEqual(self._winner('account.account_invoices_without_payment', invoice), stamp)

    def test_an_ordinary_accountant_cannot_configure_the_stamp(self):
        with self.assertRaises(AccessError):
            self.env['ncollection.document.stamp'].with_user(self.accountant).create({
                'name': 'Forged', 'company_id': self.env.company.id, 'image': _png(),
                'document_type_ids': [Command.set(self._type('customer_invoice').ids)]})
        mine = self._stamp('customer_invoice')
        with self.assertRaises(AccessError):
            mine.with_user(self.accountant).write({'x_mm': 1.0})
        with self.assertRaises(AccessError):
            mine.with_user(self.accountant).image

    def test_an_adviser_never_sees_another_companys_stamp(self):
        other = self.setup_other_company()['company']
        theirs = self._stamp('customer_invoice', company=other)
        visible = self.env['ncollection.document.stamp'].with_user(self.adviser).search([])
        self.assertNotIn(theirs, visible)
        with self.assertRaises(AccessError):
            theirs.with_user(self.adviser).write({'x_mm': 1.0})


@tagged('post_install', '-at_install')
class TestStampKindAuthorisation(_Common):
    """Who may configure a stamp for WHICH kind of document is decided by the model, not by the form."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        base = cls.env.ref('base.group_user')
        company = cls.env.company
        common = {'company_id': company.id, 'company_ids': [Command.set(company.ids)]}
        cls.adviser = Users.create(dict(common, name='Kind Adviser', login='kind_adviser', group_ids=[
            Command.set([base.id, cls.env.ref('account.group_account_manager').id])]))
        cls.only_manager = Users.create(dict(common, name='Kind Manager Only', login='kind_manager_only', group_ids=[
            Command.set([base.id, cls.env.ref('ncollection_branding.group_stamp_manager').id])]))
        cls.admin = Users.create(dict(common, name='Kind Admin', login='kind_admin', group_ids=[
            Command.set([base.id, cls.env.ref('base.group_system').id])]))
        # a kind that is not an accounting document (what a sales or purchase kind is, for this purpose)
        cls.foreign = cls.env['ncollection.document.type'].sudo().create({'name': 'Quotation', 'code': 'kind_foreign'})
        cls.accounting_codes = ('customer_invoice', 'customer_credit_note', 'vendor_bill', 'vendor_credit_note',
                                'payment_receipt')

    def _vals(self, types, **extra):
        vals = {'name': 'Kind stamp', 'company_id': self.env.company.id, 'image': _png(),
                'document_type_ids': [Command.set(types.ids)]}
        vals.update(extra)
        return vals

    def _mine(self, types, user=None):
        return self.env['ncollection.document.stamp'].with_user(user or self.adviser).create(self._vals(types))

    def test_the_adviser_may_stamp_each_of_the_five_accounting_kinds(self):
        for code in self.accounting_codes:
            stamp = self._mine(self._type(code))
            self.assertEqual(stamp.document_type_ids, self._type(code), code)
        everything = self.env['ncollection.document.type'].browse([self._type(c).id for c in self.accounting_codes])
        self.assertTrue(self._mine(everything))

    def test_the_adviser_may_not_create_a_stamp_for_any_other_kind(self):
        with self.assertRaises(AccessError):
            self._mine(self.foreign)
        with self.assertRaises(AccessError):
            self._mine(self.foreign | self._type('customer_invoice'))      # one foreign kind poisons the stamp

    def test_crafted_payloads_do_not_reach_a_foreign_kind(self):
        Stamp = self.env['ncollection.document.stamp'].with_user(self.adviser)
        ok = self._mine(self._type('customer_invoice'))
        for label, commands in (
            ('link', [Command.link(self.foreign.id)]),
            ('set', [Command.set([self._type('customer_invoice').id, self.foreign.id])]),
            ('raw tuple link', [(4, self.foreign.id)]),
            ('raw tuple set', [(6, 0, [self.foreign.id])]),
        ):
            with self.subTest(label), self.assertRaises(AccessError):
                ok.with_user(self.adviser).write({'document_type_ids': commands})
            self.assertEqual(ok.document_type_ids, self._type('customer_invoice'), 'nothing was changed')
            with self.subTest('create ' + label), self.assertRaises(AccessError):
                Stamp.create({'name': 'Crafted', 'company_id': self.env.company.id, 'image': _png(),
                              'document_type_ids': commands})
        # the same through call_kw-shaped calls and with a hostile context
        hostile = {'nc_no_stamp': True, 'nc_stamp_preview': {'stamp_id': ok.id}, 'active_test': False,
                   'install_mode': True, 'tracking_disable': True}
        with self.assertRaises(AccessError):
            Stamp.with_context(**hostile).create(self._vals(self.foreign))
        with self.assertRaises(AccessError):
            ok.with_user(self.adviser).with_context(**hostile).write(
                {'document_type_ids': [Command.link(self.foreign.id)]})
        # copy goes through create
        foreign_stamp = self.env['ncollection.document.stamp'].create(self._vals(self.foreign))
        with self.assertRaises(AccessError):
            foreign_stamp.with_user(self.adviser).copy({'image': _png()})

    def test_the_adviser_cannot_edit_or_delete_a_stamp_of_another_kind(self):
        foreign_stamp = self.env['ncollection.document.stamp'].create(self._vals(self.foreign))
        mixed = self.env['ncollection.document.stamp'].create(self._vals(self.foreign | self._type('vendor_bill')))
        for stamp in (foreign_stamp, mixed):
            with self.assertRaises(AccessError):
                stamp.with_user(self.adviser).write({'x_mm': 12.0})
            with self.assertRaises(AccessError):
                stamp.with_user(self.adviser).unlink()
            with self.assertRaises(AccessError):
                stamp.with_user(self.adviser).write(
                    {'document_type_ids': [Command.set(self._type('vendor_bill').ids)]})
        self.assertTrue(foreign_stamp.exists() and mixed.exists())

    def test_the_adviser_still_edits_and_deletes_an_accounting_stamp(self):
        stamp = self._mine(self._type('vendor_bill'))
        stamp.with_user(self.adviser).write(
            {'x_mm': 33.0, 'document_type_ids': [Command.link(self._type('vendor_credit_note').id)]})
        self.assertEqual(stamp.x_mm, 33.0)
        stamp.with_user(self.adviser).unlink()
        self.assertFalse(stamp.exists())

    def test_the_stamp_right_alone_names_no_kind(self):
        """The group that may open the designer is not, by itself, a licence to stamp every kind."""
        with self.assertRaises(AccessError):
            self._mine(self._type('customer_invoice'), user=self.only_manager)

    def test_a_settings_administrator_may_stamp_every_kind(self):
        both = self.foreign | self._type('customer_invoice')
        stamp = self._mine(both, user=self.admin)
        self.assertEqual(stamp.document_type_ids, both)
        stamp.with_user(self.admin).write({'document_type_ids': [Command.set(self.foreign.ids)]})
        stamp.with_user(self.admin).unlink()

    def test_the_kind_rule_does_not_replace_company_isolation(self):
        other = self.setup_other_company()['company']
        with self.assertRaises(AccessError):
            self.env['ncollection.document.stamp'].with_user(self.adviser).create(
                self._vals(self._type('customer_invoice'), company_id=other.id))
        theirs = self._stamp('customer_invoice', company=other)
        with self.assertRaises(AccessError):
            theirs.with_user(self.adviser).write({'x_mm': 5.0})
        with self.assertRaises(AccessError):
            theirs.with_user(self.adviser).unlink()

    def test_the_kinds_who_may_stamp_cannot_be_changed_by_the_adviser(self):
        with self.assertRaises(AccessError):
            self._type('customer_invoice').with_user(self.adviser).write(
                {'manager_group_ids': [Command.link(self.env.ref('base.group_user').id)]})
        with self.assertRaises(AccessError):
            self.foreign.with_user(self.adviser).write(
                {'manager_group_ids': [Command.link(self.env.ref('account.group_account_manager').id)]})

    def test_a_stamp_cannot_be_moved_into_a_company_the_user_does_not_belong_to(self):
        other = self.setup_other_company()['company']
        stamp = self._mine(self._type('customer_invoice'))
        with self.assertRaises(AccessError):
            stamp.with_user(self.adviser).write({'company_id': other.id})
        self.assertEqual(stamp.company_id, self.env.company)

    def test_the_preview_context_only_works_on_the_stamps_own_kind_of_document(self):
        invoice = self.init_invoice('out_invoice', post=True, amounts=[100.0], invoice_date='2026-03-01')
        report = self.env.ref('account.account_invoices')
        bill_stamp = self._mine(self._type('vendor_bill'))
        invoice_stamp = self._mine(self._type('customer_invoice'))
        Stamp = self.env['ncollection.document.stamp'].with_user(self.adviser).with_context(
            report_pdf_no_attachment=True)
        self.assertFalse(Stamp._nc_may_preview({'stamp_id': bill_stamp.id}, report, invoice),
                         'a vendor-bill stamp is never drawn on a customer invoice through the context')
        self.assertTrue(Stamp._nc_may_preview({'stamp_id': invoice_stamp.id}, report, invoice))
        # a DOWNLOAD (no report_pdf_no_attachment: Odoo stores the PDF as the document's copy) never honours it
        self.assertFalse(Stamp.with_context(report_pdf_no_attachment=False)._nc_may_preview(
            {'stamp_id': invoice_stamp.id}, report, invoice))

    def test_the_preview_of_a_stamp_of_a_kind_the_adviser_may_not_stamp_is_refused(self):
        foreign_stamp = self.env['ncollection.document.stamp'].create(self._vals(self.foreign))
        Stamp = self.env['ncollection.document.stamp'].with_user(self.adviser)
        self.assertFalse(Stamp._nc_may_preview({'stamp_id': foreign_stamp.id}))
