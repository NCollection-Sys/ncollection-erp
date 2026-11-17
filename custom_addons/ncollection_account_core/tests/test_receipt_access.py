import json

from odoo.exceptions import AccessError
from odoo.tests import HttpCase, new_test_user, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

RECEIPT_URL = '/report/pdf/account.report_payment_receipt/%s'


@tagged('post_install', '-at_install')
class TestReceiptPrintAccess(HttpCase, AccountTestInvoicingCommon):
    """Print Receipt over the REAL report route as a tenant Owner without base.group_system (production 403:
    "You are not allowed to access 'View' (ir.ui.view) records" — the template read the company layout's view key
    as the user; ir.ui.view is readable by base.group_system only).

    REPRODUCES the production 403 (RED on the old template: Owner and accountant 403; GREEN with the fix). Two things
    had hidden it: the fixture used the Standard layout, and an HttpCase request shares the test's transaction cache,
    so a layout written in setUp was already cached and its key never reached the access check. The tenant that
    failed uses Bubble, and the cache is now cleared before each request, as in a real one.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The restored tenant that answered 403 prints with Bubble (read from the shared database).
        cls.env.company.external_report_layout_id = cls.env.ref('web.external_layout_bubble')
        cls.payment = cls.env['account.payment'].create({
            'payment_type': 'inbound', 'partner_type': 'customer', 'partner_id': cls.partner_a.id, 'amount': 12.5})
        cls.payment.action_post()

    def _get(self, login):
        self.authenticate(login, login)
        # The request shares this test's transaction: drop the cached layout (written above as superuser), or the
        # template's read of its key never reaches the access check, as it does in a real request.
        self.env.invalidate_all()
        return self.url_open(RECEIPT_URL % self.payment.id)

    def test_owner_without_system_rights_prints_the_receipt(self):
        owner = new_test_user(self.env, login='nc_rcpt_owner', company_id=self.env.company.id,
                              groups='base.group_user,ncollection_core.group_role_owner,'
                                     'ncollection_core.group_tenant_admin,account.group_account_manager')
        self.assertFalse(owner.has_group('base.group_system'), "Option A: the Owner is not a technical administrator")
        response = self._get('nc_rcpt_owner')
        self.assertEqual(response.status_code, 200, response.text[:300])
        # Test mode returns the report as HTML (no wkhtmltopdf): the document itself must be in it.
        self.assertIn(self.payment.name, response.text)

    def test_accountant_prints_the_receipt(self):
        new_test_user(self.env, login='nc_rcpt_acc', groups='account.group_account_user',
                      company_id=self.env.company.id)
        response = self._get('nc_rcpt_acc')
        self.assertEqual(response.status_code, 200, response.text[:300])

    def test_a_user_who_cannot_read_payments_is_still_refused(self):
        new_test_user(self.env, login='nc_rcpt_emp', groups='base.group_user')
        response = self._get('nc_rcpt_emp')
        self.assertEqual(response.status_code, 403)
        self.assertIn('account.payment', response.text, "refused on the business record, not on a technical view")


@tagged('post_install', '-at_install')
class TestPlatformReceiptCapability(TestReceiptPrintAccess):
    """A Workspace Administrator with NO accounting role, three ways, over the real report route:

      granted   the platform pushed receipts=granted -> 200 (Odoo's read-only accounting, implied by the admin group)
      denied    the platform pushed nothing / denied  -> 403 on account.payment, exactly as before
      plain     an ordinary user, whatever the platform pushed -> 403
    and the grant never makes anyone a technical administrator, and revoking it removes the access at once."""

    def _push(self, granted):
        config = self.env['ncollection.workspace.config'].sudo().search([], limit=1) or \
            self.env['ncollection.workspace.config'].sudo().create({})
        config.write({'settings_policy': json.dumps({'receipts': 'granted' if granted else 'denied'})})

    def _admin(self, login):
        return new_test_user(self.env, login=login, company_id=self.env.company.id,
                             groups='base.group_user,ncollection_core.group_tenant_admin')

    def test_granted_admin_prints(self):
        admin = self._admin('nc_cap_granted')
        self._push(True)
        self.assertTrue(admin.has_group('account.group_account_readonly'))
        self.assertFalse(admin.has_group('base.group_system'))
        self.assertFalse(admin.has_group('account.group_account_invoice'), "read only: no invoicing / payment rights")
        response = self._get('nc_cap_granted')
        self.assertEqual(response.status_code, 200, response.text[:300])
        self.assertIn(self.payment.name, response.text)

    def test_restricted_admin_is_refused(self):
        self._admin('nc_cap_denied')
        self._push(False)
        response = self._get('nc_cap_denied')
        self.assertEqual(response.status_code, 403)
        self.assertIn('account.payment', response.text)

    def test_revoking_removes_it_at_once(self):
        admin = self._admin('nc_cap_revoked')
        self._push(True)
        self.assertTrue(admin.has_group('account.group_account_readonly'))
        self._push(False)
        self.env.invalidate_all()
        self.assertFalse(admin.has_group('account.group_account_readonly'))
        self.assertEqual(self._get('nc_cap_revoked').status_code, 403)

    def test_ordinary_user_is_refused_even_when_granted(self):
        new_test_user(self.env, login='nc_cap_plain', groups='base.group_user', company_id=self.env.company.id)
        self._push(True)
        self.assertEqual(self._get('nc_cap_plain').status_code, 403)

    def test_tenant_cannot_grant_itself(self):
        admin = self._admin('nc_cap_self')
        config = self.env['ncollection.workspace.config'].sudo().search([], limit=1) or \
            self.env['ncollection.workspace.config'].sudo().create({})
        with self.assertRaises(AccessError):
            config.with_user(admin).write({'settings_policy': json.dumps({'receipts': 'granted'})})


@tagged('post_install', '-at_install')
class TestSupplierWordingInCode(AccountTestInvoicingCommon):
    """The Odoo Python strings reworded by extension follow the platform rule (Vendor -> Supplier)."""

    def test_draft_refund_name_and_journal_placeholder(self):
        refund = self.env['account.move'].with_context(lang='en_US').create({
            'move_type': 'in_refund', 'partner_id': self.partner_a.id})
        self.assertIn('Supplier', refund.with_context(lang='en_US')._get_move_display_name())
        self.assertNotIn('Vendor', refund.with_context(lang='en_US')._get_move_display_name())
        journal = self.env['account.journal'].with_context(lang='en_US').new({'type': 'purchase'})
        self.assertTrue(journal.name_placeholder.startswith('Supplier Bills'), journal.name_placeholder)

    def test_three_more_returned_strings(self):
        bill = self.env['account.move'].with_context(lang='en_US').create({
            'move_type': 'in_invoice', 'partner_id': self.partner_a.id})
        self.assertEqual(bill._creation_message(), 'Supplier Bill Created')
        # an existing plan: a NEW plan adds a column at runtime (x_plan<N>_id), which the schema-drift check reports
        analytic_plan = self.env['account.analytic.plan'].search([], limit=1)
        self.assertTrue(analytic_plan)
        analytic = self.env['account.analytic.account'].create({'name': 'A', 'plan_id': analytic_plan.id})
        self.assertEqual(analytic.with_context(lang='en_US').action_view_vendor_bill()['name'], 'Supplier Bills')
        label = self.env['product.supplierinfo'].with_context(lang='en_US').get_import_templates()[0]['label']
        self.assertNotIn('Vendor', label)
