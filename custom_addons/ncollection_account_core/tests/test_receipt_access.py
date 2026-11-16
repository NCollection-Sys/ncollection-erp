from odoo.tests import HttpCase, new_test_user, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

RECEIPT_URL = '/report/pdf/account.report_payment_receipt/%s'


@tagged('post_install', '-at_install')
class TestReceiptPrintAccess(HttpCase, AccountTestInvoicingCommon):
    """Print Receipt over the REAL report route as a tenant Owner without base.group_system (production 403:
    "You are not allowed to access 'View' (ir.ui.view) records" — the template read the company layout's view key
    as the user; ir.ui.view is readable by base.group_system only).

    NOTE: a fresh test database does NOT reproduce the 403 (verified: these pass without the fix too). The fix was
    proven on a restored copy of a real tenant over HTTP: 403 + traceback before, 200 + PDF after, nothing else
    changed. These tests guard the allowed / refused outcomes, not the original trigger."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company.external_report_layout_id = cls.env.ref('web.external_layout_standard')
        cls.payment = cls.env['account.payment'].create({
            'payment_type': 'inbound', 'partner_type': 'customer', 'partner_id': cls.partner_a.id, 'amount': 12.5})
        cls.payment.action_post()

    def _get(self, login):
        self.authenticate(login, login)
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
