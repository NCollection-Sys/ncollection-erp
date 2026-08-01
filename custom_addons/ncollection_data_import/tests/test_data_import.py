# -*- coding: utf-8 -*-
"""P3-T11: the tenant onboarding import toolkit.

Proves the acceptance — "the full template set imports into a fresh tenant
without developer help" — by driving the SHIPPED templates through the real
``base_import`` path (the same path the native UI uses) and asserting the
records land, opening stock applies to on-hand, and opening balances produce a
balanced opening move. The toolkit only guides + validates; Odoo core imports.
"""
import base64

from odoo import tools
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.ncollection_data_import.wizard.onboarding import (
    ENTITY_CONTEXT, IMPORT_OPTS, nc_friendly_error)
from odoo.tests import tagged

TEMPLATE = 'ncollection_data_import/data/templates/%s.csv'


@tagged('post_install', '-at_install')
class TestTenantDataImport(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Onboarding = cls.env['ncollection.data.import.onboarding']
        # A real provisioned tenant's first warehouse is code "WH" → its stock
        # location is "WH/Stock" (what the shipped opening_stock.csv references).
        # The accounting test company uses a different code, so normalise it to
        # "WH" here to mirror a real fresh tenant.
        cls.warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.env.company.id)], limit=1)
        if not cls.warehouse:
            cls.warehouse = cls.env['stock.warehouse'].create({
                'name': 'Main', 'code': 'WH', 'company_id': cls.env.company.id})
        elif cls.warehouse.code != 'WH':
            cls.warehouse.code = 'WH'
        cls.stock_loc = cls.warehouse.lot_stock_id

    # ---- helpers --------------------------------------------------------

    def _template_bytes(self, entity):
        with tools.file_open(TEMPLATE % entity, 'rb') as fh:
            return fh.read()

    def _do_import(self, model, csv_bytes, entity=None, dryrun=False):
        """Import through the real base_import path, mapping columns exactly as
        the wizard does (incl. the id/.id fix)."""
        ctx = ENTITY_CONTEXT.get(entity, {}) if entity else {}
        rec = self.env['base_import.import'].with_context(**ctx).create({
            'res_model': model, 'file': csv_bytes,
            'file_type': 'text/csv', 'file_name': (entity or model) + '.csv'})
        # dict(IMPORT_OPTS) per call — base_import mutates its options arg
        # in place (see wizard._dry_run), so never share the constant object.
        preview = rec.parse_preview(dict(IMPORT_OPTS))
        self.assertFalse(preview.get('error'), preview.get('error'))
        columns = self.Onboarding._map_columns(
            preview['headers'], preview.get('matches') or {})
        return rec.execute_import(columns, preview['headers'], dict(IMPORT_OPTS), dryrun=dryrun)

    def _validate(self, entity, csv_bytes):
        wiz = self.Onboarding.create({
            'entity': entity, 'data_file': base64.b64encode(csv_bytes),
            'data_fname': entity + '.csv'})
        wiz.action_validate()
        return wiz.result_html or ''

    # ---- the shipped templates import ---------------------------------

    def test_products_template_imports(self):
        res = self._do_import('product.template', self._template_bytes('products'), 'products')
        self.assertFalse([m for m in res['messages'] if m['type'] == 'error'], res['messages'])
        self.assertEqual(len(res['ids']), 3)
        paper = self.env['product.template'].search([('default_code', '=', 'PAP-A4-BOX')])
        self.assertTrue(paper.is_storable)
        self.assertAlmostEqual(paper.list_price, 45.0)

    def test_customers_and_suppliers_import(self):
        cres = self._do_import('res.partner', self._template_bytes('customers'), 'customers')
        self.assertFalse([m for m in cres['messages'] if m['type'] == 'error'], cres['messages'])
        cust = self.env['res.partner'].search([('name', '=', 'Al Barari Trading LLC')])
        self.assertTrue(cust.customer_rank >= 1 and cust.is_company)
        sres = self._do_import('res.partner', self._template_bytes('suppliers'), 'suppliers')
        self.assertFalse([m for m in sres['messages'] if m['type'] == 'error'], sres['messages'])
        supp = self.env['res.partner'].search([('name', '=', 'Gulf Office Supplies FZE')])
        self.assertTrue(supp.supplier_rank >= 1)

    def test_shipped_opening_stock_imports_and_applies(self):
        # the LITERAL shipped files: products first, then opening_stock (which
        # references those products by name + the WH/Stock location).
        self._do_import('product.template', self._template_bytes('products'), 'products')
        self.assertEqual(self.stock_loc.complete_name, 'WH/Stock')
        res = self._do_import('stock.quant', self._template_bytes('opening_stock'), 'opening_stock')
        self.assertFalse([m for m in res['messages'] if m['type'] == 'error'], res['messages'])
        quants = self.env['stock.quant'].browse(res['ids'])
        quants.with_context(**ENTITY_CONTEXT['opening_stock']).action_apply_inventory()
        paper = self.env['product.product'].search([('default_code', '=', 'PAP-A4-BOX')], limit=1)
        self.assertAlmostEqual(paper.qty_available, 120.0)

    def test_opening_balances_import_produces_balanced_move(self):
        # the mechanism, keyed by database id (the shipped file documents the
        # external-id export-then-reimport flow it can't hardcode).
        recv = self.company_data['default_account_receivable']
        pay = self.company_data['default_account_payable']
        csv = (".id,opening_debit,opening_credit\n"
               "%d,25000.00,0.00\n%d,0.00,25000.00\n" % (recv.id, pay.id)).encode()
        res = self._do_import('account.account', csv, 'opening_balances')
        self.assertFalse([m for m in res['messages'] if m['type'] == 'error'], res['messages'])
        self.assertAlmostEqual(recv.opening_debit, 25000.0)
        move = self.env.company.account_opening_move_id
        self.assertTrue(move, "an opening move was created")
        self.assertAlmostEqual(sum(move.line_ids.mapped('debit')),
                               sum(move.line_ids.mapped('credit')))  # Dr == Cr

    def test_all_shipped_templates_map_to_real_fields(self):
        expected = {
            'customers': 'res.partner', 'suppliers': 'res.partner',
            'products': 'product.template', 'opening_stock': 'stock.quant',
            'opening_balances': 'account.account',
        }
        for entity, model in expected.items():
            rec = self.env['base_import.import'].create({
                'res_model': model, 'file': self._template_bytes(entity),
                'file_type': 'text/csv', 'file_name': entity + '.csv'})
            preview = rec.parse_preview(dict(IMPORT_OPTS))
            self.assertFalse(preview.get('error'), "%s: %s" % (entity, preview.get('error')))
            columns = self.Onboarding._map_columns(
                preview['headers'], preview.get('matches') or {})
            self.assertTrue(all(columns), "%s unmapped columns: %s" % (entity, columns))

    # ---- validation + friendly errors (the acceptance clause) ---------

    def test_validate_good_file_reports_success(self):
        self.assertIn('Looks good', self._validate('products', self._template_bytes('products')))

    def test_validate_does_not_mutate_shared_options(self):
        # base_import stamps the detected 'encoding' INTO its options dict; the
        # wizard must pass a copy, or one file's guessed encoding leaks into every
        # later validation in the worker. Assert the shared constant stays clean.
        self.assertNotIn('encoding', IMPORT_OPTS)
        self._validate('customers', self._template_bytes('customers'))
        self.assertNotIn('encoding', IMPORT_OPTS)

    def test_validate_bad_file_reports_friendly_error(self):
        bad = ('product_id,location_id,inventory_quantity\n'
               '"Nope","No/Such/Location",5\n').encode()
        html = self._validate('opening_stock', bad)
        self.assertIn('issue', html.lower())
        self.assertNotIn('Looks good', html)

    def test_shipped_opening_balances_placeholder_is_caught_not_greenlit(self):
        # THE regression the reviewer found: the shipped opening_balances.csv must
        # NOT validate as "Looks good" — its placeholder External IDs would (if
        # left in) collapse rows onto one account. The pre-flight flags it.
        html = self._validate('opening_balances', self._template_bytes('opening_balances'))
        self.assertNotIn('Looks good', html)
        self.assertIn('placeholder', html.lower())

    def test_duplicate_external_id_is_flagged(self):
        dup = ("id,opening_debit,opening_credit\n"
               "myco.acc_x,100,0\n"
               "myco.acc_x,0,100\n").encode()   # same id twice → silent merge
        html = self._validate('opening_balances', dup)
        self.assertNotIn('Looks good', html)
        self.assertIn('overwrite each other', html.lower())

    def test_friendly_error_translates_common_cases(self):
        self.assertIn("doesn't match", nc_friendly_error("No matching record found for X"))
        self.assertIn("duplicate", nc_friendly_error("A record already exists").lower())
        self.assertEqual(nc_friendly_error("some novel error"), "some novel error")

    # ---- wizard actions ----------------------------------------------

    def test_download_template_returns_csv_url(self):
        wiz = self.Onboarding.create({'entity': 'customers'})
        action = wiz.action_download_template()
        self.assertEqual(action['type'], 'ir.actions.act_url')
        self.assertIn('/web/content/', action['url'])

    def test_open_import_targets_the_right_model(self):
        wiz = self.Onboarding.create({'entity': 'opening_balances'})
        self.assertEqual(wiz.action_open_import()['res_model'], 'account.account')
        wiz.entity = 'opening_stock'
        action = wiz.action_open_import()
        self.assertEqual(action['res_model'], 'stock.quant')
        self.assertTrue(action['context'].get('inventory_mode'))
