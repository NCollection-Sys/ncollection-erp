# -*- coding: utf-8 -*-
"""Live recalculation of charges / discounts while editing, edge cases (currency, tax-included, rounding, many
adjustments, no duplicates) and the invoice column preferences (company default + user override)."""

from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import Form, tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged('post_install', '-at_install')
class TestInvoiceLiveAdjustments(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.other_currency = cls.setup_other_currency('EUR')
        income = cls.company_data['default_account_revenue']
        Type = cls.env['ncollection.invoice.adjustment.type']
        cls.freight = Type.create(
            {
                'name': 'Freight live',
                'kind': 'charge',
                'value_mode': 'fixed',
                'account_id': income.id,
                'tax_ids': [Command.set(cls.tax_sale_a.ids)],
            }
        )
        cls.pct_discount = Type.create(
            {
                'name': 'Global % live',
                'kind': 'discount',
                'value_mode': 'percent',
                'account_id': income.id,
                'tax_ids': [Command.set(cls.tax_sale_a.ids)],
            }
        )
        cls.pct_charge = Type.create(
            {
                'name': 'Handling live',
                'kind': 'charge',
                'value_mode': 'percent',
                'basis': 'lines_after_discount',
                'account_id': income.id,
            }
        )

    def _move(self, qty=2.0, price=100.0, **extra):
        vals = {
            'move_type': 'out_invoice',
            'partner_id': self.partner_a.id,
            'invoice_date': '2026-03-01',
            'invoice_line_ids': [
                Command.create(
                    {'name': 'L', 'quantity': qty, 'price_unit': price, 'tax_ids': [Command.set(self.tax_sale_a.ids)]}
                )
            ],
        }
        vals.update(extra)
        return self.env['account.move'].create(vals)

    def _adjust(self, move, type_, value):
        return self.env['ncollection.invoice.adjustment'].create(
            {'move_id': move.id, 'type_id': type_.id, 'value': value}
        )

    # --- live recalculation ----------------------------------------------------------------------------------------
    def test_editing_a_line_reprices_a_percentage_adjustment_before_saving(self):
        move = self._move()  # 200
        self._adjust(move, self.pct_discount, 10.0)  # -20
        self.assertEqual(move.amount_untaxed, 180.0)
        with Form(move) as form:
            with form.invoice_line_ids.edit(0) as line:
                line.quantity = 4.0  # 400
            # nothing saved yet: the form already shows the new discount and totals
            self.assertEqual(form.nc_adjustment_ids._records[0]['amount'], 40.0)
            self.assertEqual(form.nc_adjusted_untaxed, 360.0)
            booked = [r for r in form.invoice_line_ids._records if r.get('price_unit') and r['price_unit'] < 0]
            self.assertEqual(booked[0]['price_unit'], -40.0)  # the booked discount line already follows
            self.assertEqual(form.nc_adjusted_untaxed, 360.0)
        self.assertEqual(move.amount_untaxed, 360.0)
        self.assertEqual(len(move.invoice_line_ids.filtered('nc_adjustment_id')), 1)

    # --- the money Discount typed IN THE FORM (the onchange path; the ORM tests only reach the inverse) -----------
    def _product_line(self, move):
        return move.invoice_line_ids.filtered(lambda line: line.display_type == 'product')

    def test_typing_the_money_discount_in_the_form_drives_the_percentage(self):
        move = self._move(qty=2.0, price=200.0)  # 400
        with Form(move) as form:
            with form.invoice_line_ids.edit(0) as line:
                line.nc_discount_amount = 100.0
                # found on the real application: the onchange answered the OLD amount and Disc.% never moved
                self.assertEqual(line.nc_discount_amount, 100.0)
                self.assertEqual(line.discount, 25.0)
                line.quantity = 4.0  # 800: a fixed discount stays the same money, the percentage follows
                self.assertEqual(line.nc_discount_amount, 100.0)
                self.assertEqual(line.discount, 12.5)
        line = self._product_line(move)
        self.assertEqual(line.nc_discount_mode, 'amount')
        self.assertEqual(line.nc_discount_fixed, 100.0)
        self.assertEqual(line.price_subtotal, 700.0)

    def test_typing_the_percentage_in_the_form_after_a_fixed_discount_returns_to_percentage_mode(self):
        move = self._move(qty=2.0, price=200.0)
        with Form(move) as form:
            with form.invoice_line_ids.edit(0) as line:
                line.nc_discount_amount = 100.0
                line.discount = 10.0
                self.assertEqual(line.nc_discount_amount, 40.0)
                line.quantity = 4.0  # a percentage discount follows the line value
                self.assertEqual(line.nc_discount_amount, 80.0)
        line = self._product_line(move)
        self.assertEqual(line.nc_discount_mode, 'percent')
        self.assertEqual(line.discount, 10.0)
        self.assertEqual(line.price_subtotal, 720.0)

    # --- what the WEB CLIENT really sends (found in the real browser; odoo.tests.Form sends more fields) ----------
    def test_saving_a_quantity_edit_keeps_a_fixed_discount_fixed(self):
        move = self._move(qty=2.0, price=200.0)
        line = self._product_line(move)
        line.nc_discount_amount = 100.0
        # the browser saves the quantity together with the Disc.% that merely followed it, and no discount mode
        sent = {'quantity': 4.0, 'discount': 12.5, 'nc_amount': 700.0}
        move.write({'invoice_line_ids': [Command.update(line.id, sent)]})
        self.assertEqual(line.nc_discount_mode, 'amount')
        self.assertEqual(line.nc_discount_fixed, 100.0)
        self.assertEqual(line.discount, 12.5)
        self.assertEqual(line.price_subtotal, 700.0)
        # a percentage the fixed amount does NOT give is a typed percentage
        move.write({'invoice_line_ids': [Command.update(line.id, {'discount': 10.0})]})
        self.assertEqual(line.nc_discount_mode, 'percent')
        self.assertEqual(line.price_subtotal, 720.0)

    def test_a_total_that_followed_a_quantity_edit_never_moves_the_price(self):
        move = self._move(qty=4.0, price=200.0)
        line = self._product_line(move)
        line.discount = 10.0
        names = ('quantity', 'price_unit', 'discount', 'nc_discount_mode', 'nc_discount_fixed', 'nc_discount_percent',
                 'nc_discount_amount', 'nc_amount', 'nc_total_incl', 'price_subtotal', 'price_total')
        # quantity 4 -> 3 while the client still holds the figures of quantity 4
        sent = {'quantity': 3.0, 'discount': 10.0, 'nc_discount_percent': 10.0, 'nc_discount_amount': 80.0,
                'nc_amount': line.nc_amount, 'nc_total_incl': line.nc_total_incl,
                'price_subtotal': line.price_subtotal, 'move_id': {'id': move.id}}
        value = line.onchange(sent, ['quantity'], {name: {} for name in names})['value']
        self.assertNotIn('price_unit', value, "the unit price drifted to 200.32 here")
        self.assertEqual(value['nc_amount'], 540.0)
        self.assertEqual(value['nc_discount_amount'], 60.0)

    def test_the_price_solver_is_exact_on_a_discounted_multi_quantity_line(self):
        move = self._move(qty=3.0, price=150.0)
        line = self._product_line(move)
        line.discount = 10.0
        for price in (200.0, 204.0, 1234.56):
            line.price_unit = price
            total, amount = line.price_total, line.price_subtotal
            line.price_unit = 150.0
            self.assertEqual(line._nc_solve_price('total', total), (price, total, True))
            self.assertEqual(line._nc_solve_price('amount', amount), (price, amount, True))

    def test_changing_the_adjustment_value_reprices_totals_before_saving(self):
        move = self._move()
        adj = self._adjust(move, self.freight, 10.0)
        with Form(move) as form:
            with form.nc_adjustment_ids.edit(0) as row:
                row.value = 40.0
            self.assertEqual(form.nc_adjustment_ids._records[0]['amount'], 40.0)
            self.assertEqual(form.nc_adjusted_untaxed, 240.0)
        self.assertEqual(adj.amount, 40.0)
        self.assertEqual(move.amount_untaxed, 240.0)

    def test_saving_repeatedly_never_duplicates_adjustment_lines(self):
        move = self._move()
        self._adjust(move, self.freight, 10.0)
        self._adjust(move, self.pct_discount, 5.0)
        for _ in range(3):
            with Form(move) as form:
                with form.invoice_line_ids.edit(0) as line:
                    line.quantity = line.quantity + 1
            move.write({'invoice_line_ids': []})  # a no-op write must not rebook
        self.assertEqual(len(move.invoice_line_ids.filtered('nc_adjustment_id')), 2)
        self.assertEqual(
            move.line_ids.filtered(lambda ln: ln.nc_adjustment_id and ln.display_type == 'product')[:1].move_id, move
        )

    def test_many_adjustments_keep_the_documented_order_and_balance(self):
        move = self._move(qty=3.0, price=33.33)  # 99.99
        self._adjust(move, self.pct_discount, 7.0)
        self._adjust(move, self.freight, 12.5)
        self._adjust(move, self.pct_charge, 3.0)
        currency = move.currency_id
        basis = 99.99
        discount = currency.round(basis * 0.07)
        handling = currency.round((basis - discount) * 0.03)
        self.assertEqual(move.amount_untaxed, currency.round(basis - discount + 12.5 + handling))
        move.action_post()
        self.assertEqual(currency.round(sum(move.line_ids.mapped('balance'))), 0.0)

    def test_foreign_currency_invoice_balances(self):
        move = self._move(currency_id=self.other_currency.id)
        self._adjust(move, self.freight, 15.0)
        self._adjust(move, self.pct_discount, 12.5)
        move.action_post()
        self.assertEqual(move.company_currency_id.round(sum(move.line_ids.mapped('balance'))), 0.0)
        self.assertEqual(sum(move.line_ids.mapped('amount_currency')), 0.0)

    def test_tax_included_prices_keep_the_documented_totals(self):
        tax = self.tax_sale_a.copy({'name': 'Incl 15', 'price_include_override': 'tax_included'})
        move = self._move()
        move.invoice_line_ids.tax_ids = [Command.set(tax.ids)]
        self._adjust(move, self.pct_discount, 10.0)
        move.action_post()
        self.assertEqual(move.company_currency_id.round(sum(move.line_ids.mapped('balance'))), 0.0)
        self.assertAlmostEqual(move.amount_total, 180.0, places=2)

    def test_vendor_bill_and_refund_recalculate(self):
        bill = self._move(move_type='in_invoice')
        bill.invoice_line_ids.tax_ids = [Command.set(self.tax_purchase_a.ids)]
        vendor_type = self.env['ncollection.invoice.adjustment.type'].create(
            {
                'name': 'Bill freight live',
                'kind': 'charge',
                'value_mode': 'percent',
                'account_id': self.company_data['default_account_expense'].id,
                'applies_to': 'vendor',
            }
        )
        self._adjust(bill, vendor_type, 10.0)
        with Form(bill) as form:
            with form.invoice_line_ids.edit(0) as line:
                line.quantity = 5.0
            self.assertEqual(form.nc_adjustment_ids._records[0]['amount'], 50.0)
        self.assertEqual(bill.amount_untaxed, 550.0)
        bill.action_post()
        reversal = bill._reverse_moves([{'invoice_date': '2026-03-02'}])
        # the credit note carries the charge as an adjustment linked to the ONE line Odoo copied: never doubled
        self.assertEqual(len(reversal.nc_adjustment_ids), 1)
        self.assertEqual(len(reversal.invoice_line_ids.filtered('nc_adjustment_id')), 1)
        self.assertEqual(reversal.amount_untaxed, bill.amount_untaxed)

    def test_posted_invoice_adjustments_stay_protected(self):
        move = self._move()
        adj = self._adjust(move, self.freight, 10.0)
        move.action_post()
        with self.assertRaises(Exception):
            adj.write({'value': 99.0})
        self.assertEqual(adj.amount, 10.0)
        self.assertEqual(move.amount_untaxed, 210.0)

    def test_configuration_changes_are_tracked_in_the_chatter(self):
        # the tracking MECHANISM is wired (the test transaction disables message creation; the committed-write proof is
        # in the release rehearsal): a mail thread whose business fields are tracked
        types = self.env['ncollection.invoice.adjustment.type']
        self.assertIn('mail.thread', types._inherit if isinstance(types._inherit, list) else [types._inherit])
        self.assertTrue({'name', 'kind', 'default_value', 'active'} <= types._track_get_fields())


@tagged('post_install', '-at_install')
class TestInvoiceColumnPreferences(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group = lambda xid: cls.env.ref(xid).id  # noqa: E731
        cls.accountant = cls.env['res.users'].create(
            {
                'name': 'pref_user',
                'login': 'pref_user',
                'password': 'Pref-User-Pass1!',
                'group_ids': [Command.set([group('base.group_user'), group('account.group_account_invoice')])],
            }
        )
        cls.other = cls.env['res.users'].create(
            {
                'name': 'pref_other',
                'login': 'pref_other',
                'password': 'Pref-Other-Pass1!',
                'group_ids': [Command.set([group('base.group_user'), group('account.group_account_invoice')])],
            }
        )
        cls.manager = cls.env['res.users'].create(
            {
                'name': 'pref_manager',
                'login': 'pref_manager',
                'password': 'Pref-Mgr-Pass1!',
                'group_ids': [Command.set([group('base.group_user'), group('account.group_account_manager')])],
            }
        )

    def pref(self, user):
        return self.env['ncollection.invoice.column.pref'].with_user(user)

    def test_default_is_the_view_then_company_then_user(self):
        self.assertEqual(self.pref(self.accountant).nc_get()['source'], 'view')
        self.pref(self.manager).nc_save('invoice_lines', ['quantity', 'name'], ['discount'], 'company')
        got = self.pref(self.accountant).nc_get()
        self.assertEqual(
            (got['source'], got['order'], got['hidden']), ('company', ['quantity', 'name'], ['discount'])
        )
        self.pref(self.accountant).nc_save('invoice_lines', ['name', 'quantity'], [], 'user')
        got = self.pref(self.accountant).nc_get()
        self.assertEqual((got['source'], got['order'], got['hidden']), ('user', ['name', 'quantity'], []))
        # another user still gets the company default, not someone else's choice
        self.assertEqual(self.pref(self.other).nc_get()['order'], ['quantity', 'name'])
        self.pref(self.accountant).nc_reset()
        self.assertEqual(self.pref(self.accountant).nc_get()['source'], 'company')

    def test_only_a_manager_sets_the_company_default(self):
        with self.assertRaises(AccessError):
            self.pref(self.accountant).nc_save('invoice_lines', ['name'], [], 'company')
        self.assertTrue(self.pref(self.manager).nc_get()['can_company'])
        self.assertFalse(self.pref(self.accountant).nc_get()['can_company'])

    def test_a_user_cannot_touch_another_users_preference_or_the_company_row_directly(self):
        self.pref(self.accountant).nc_save('invoice_lines', ['name'], [], 'user')
        self.pref(self.manager).nc_save('invoice_lines', ['quantity'], [], 'company')
        rows = self.env['ncollection.invoice.column.pref'].search([])
        mine = rows.filtered(lambda r: r.user_id == self.accountant)
        company = rows.filtered(lambda r: not r.user_id)
        with self.assertRaises(AccessError):
            mine.with_user(self.other).write({'column_order': 'name'})
        with self.assertRaises(AccessError):
            company.with_user(self.accountant).write({'column_order': 'name'})
        with self.assertRaises(AccessError):
            self.env['ncollection.invoice.column.pref'].with_user(self.other).create(
                {'user_id': self.manager.id, 'column_order': 'name'}
            )
        self.assertFalse(mine.with_user(self.other).search([('id', '=', mine.id)]))

    def test_unknown_columns_are_refused_and_names_are_cleaned(self):
        with self.assertRaises(ValidationError):
            self.pref(self.manager).nc_save('invoice_lines', ['no_such_field'], [], 'company')
        got = self.pref(self.manager).nc_save('invoice_lines', ['name', 'name', '', 'quantity'], [], 'user')
        self.assertEqual(got['order'], ['name', 'quantity'])

    def test_a_company_default_change_is_tracked(self):
        prefs = self.env['ncollection.invoice.column.pref']
        self.assertIn('mail.thread', prefs._inherit)
        self.assertTrue({'column_order', 'column_hidden'} <= prefs._track_get_fields())

    def test_the_menus_for_configuration_are_manager_only(self):
        menu = self.env.ref('ncollection_account_invoice_layout.menu_nc_colpref')
        visible = self.env['ir.ui.menu'].with_user(self.accountant)._visible_menu_ids()
        self.assertNotIn(menu.id, visible)
        self.assertIn(menu.id, self.env['ir.ui.menu'].with_user(self.manager)._visible_menu_ids())
