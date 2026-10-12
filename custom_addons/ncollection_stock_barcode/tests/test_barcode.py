# -*- coding: utf-8 -*-
"""The barcode workflow runs on Odoo's own stock engine: scans change what a person would change by hand, validation
is the engine's, rights and companies are the caller's."""
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

SVC = 'ncollection.stock.barcode'


@tagged('post_install', '-at_install')
class TestStockBarcode(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.company.id)], limit=1)
        cls.stock_loc = cls.warehouse.lot_stock_id
        cls.supplier = cls.env.ref('stock.stock_location_suppliers')
        cls.customer = cls.env.ref('stock.stock_location_customers')
        cls.partner = cls.env['res.partner'].create({'name': 'Barcode Partner'})
        Product = cls.env['product.product']
        cls.prod_a = Product.create({'name': 'Widget A', 'is_storable': True, 'barcode': '6291041500213'})
        cls.prod_b = Product.create({'name': 'Widget B', 'is_storable': True, 'barcode': '6291041500220'})
        cls.prod_lot = Product.create({'name': 'Lot Widget', 'is_storable': True, 'tracking': 'lot',
                                       'barcode': '6291041500237'})
        cls.shelf = cls.env['stock.location'].create({
            'name': 'Shelf A1', 'location_id': cls.stock_loc.id, 'barcode': 'LOC-A1', 'usage': 'internal'})
        other_tree = cls.env['stock.location'].create({'name': 'Another Tree', 'usage': 'view'})
        cls.elsewhere = cls.env['stock.location'].create({
            'name': 'Elsewhere', 'location_id': other_tree.id, 'barcode': 'LOC-ELSE', 'usage': 'internal'})
        cls.warehouse_user = cls._user('bc_wh', 'stock.group_stock_user')
        cls.manager = cls._user('bc_mgr', 'stock.group_stock_manager')
        cls.employee = cls._user('bc_emp', 'base.group_user')

    @classmethod
    def _user(cls, login, group):
        return cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': login, 'login': login, 'company_id': cls.company.id, 'company_ids': [(6, 0, cls.company.ids)],
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, cls.env.ref(group).id])]})

    def _picking(self, kind, lines):
        picking_type = {'in': self.warehouse.in_type_id, 'out': self.warehouse.out_type_id,
                        'int': self.warehouse.int_type_id}[kind]
        src, dest = {'in': (self.supplier, self.stock_loc), 'out': (self.stock_loc, self.customer),
                     'int': (self.stock_loc, self.shelf)}[kind]
        picking = self.env['stock.picking'].create({
            'picking_type_id': picking_type.id, 'partner_id': self.partner.id,
            'location_id': src.id, 'location_dest_id': dest.id,
            'move_ids': [(0, 0, {'product_id': p.id, 'product_uom_qty': q, 'product_uom': p.uom_id.id,
                                 'location_id': src.id, 'location_dest_id': dest.id})
                         for p, q in lines]})
        picking.action_confirm()
        return picking

    def _svc(self, user=None):
        return self.env[SVC].with_user(user) if user else self.env[SVC]

    def _stock(self, product, location=None):
        return self.env['stock.quant']._get_available_quantity(product, location or self.stock_loc)

    # ------------------------------------------------------------------ receipts
    def test_a_receipt_is_scanned_and_validated_with_the_stock_engines_own_validation(self):
        picking = self._picking('in', [(self.prod_a, 3)])
        svc = self._svc(self.warehouse_user)
        self.assertTrue(svc.nc_barcode_open(picking.id)['ok'])
        for _ in range(3):
            reply = svc.nc_barcode_scan(picking.id, self.prod_a.barcode)
            self.assertTrue(reply['ok'], reply)
        line = reply['state']['lines'][0]
        self.assertEqual((line['picked'], line['demand'], line['complete']), (3.0, 3.0, True))
        self.assertTrue(reply['state']['complete'])
        done = svc.nc_barcode_validate(picking.id)
        self.assertTrue(done['done'])
        self.assertEqual(picking.state, 'done')
        self.assertEqual(self._stock(self.prod_a), 3.0)

    def test_a_partial_receipt_hands_back_the_engines_backorder_decision(self):
        picking = self._picking('in', [(self.prod_a, 5)])
        svc = self._svc(self.warehouse_user)
        svc.nc_barcode_open(picking.id)
        svc.nc_barcode_scan(picking.id, self.prod_a.barcode, qty=2)
        reply = svc.nc_barcode_validate(picking.id)
        self.assertFalse(reply['done'])
        self.assertEqual(reply['action']['res_model'], 'stock.backorder.confirmation')
        self.assertEqual(picking.state, 'assigned')               # nothing was decided for the user

    def test_nothing_is_validated_without_a_scan(self):
        picking = self._picking('in', [(self.prod_a, 1)])
        with self.assertRaises(UserError):
            self._svc(self.warehouse_user).nc_barcode_validate(picking.id)

    def test_unknown_foreign_and_empty_barcodes_are_explained_not_applied(self):
        picking = self._picking('in', [(self.prod_a, 2)])
        svc = self._svc(self.warehouse_user)
        svc.nc_barcode_open(picking.id)
        for code, fragment in (('000UNKNOWN', 'not a product'), (self.prod_b.barcode, 'not part of'),
                               ('  ', 'Nothing')):
            reply = svc.nc_barcode_scan(picking.id, code)
            self.assertFalse(reply['ok'], code)
            self.assertIn(fragment, reply['message'])
        self.assertEqual(reply['state']['lines'][0]['picked'], 0.0)

    def test_scanning_more_than_demanded_is_allowed_but_flagged(self):
        picking = self._picking('in', [(self.prod_a, 1)])
        svc = self._svc(self.warehouse_user)
        svc.nc_barcode_open(picking.id)
        svc.nc_barcode_scan(picking.id, self.prod_a.barcode)
        reply = svc.nc_barcode_scan(picking.id, self.prod_a.barcode)
        self.assertTrue(reply['ok'])
        self.assertEqual(reply['level'], 'warning')

    def test_a_mis_scan_is_corrected_without_touching_the_rest(self):
        picking = self._picking('in', [(self.prod_a, 4), (self.prod_b, 2)])
        svc = self._svc(self.warehouse_user)
        svc.nc_barcode_open(picking.id)
        svc.nc_barcode_scan(picking.id, self.prod_a.barcode, qty=3)
        svc.nc_barcode_scan(picking.id, self.prod_b.barcode, qty=1)
        move_a = picking.move_ids.filtered(lambda m: m.product_id == self.prod_a)
        reply = svc.nc_barcode_adjust(picking.id, move_a.id, -1)
        by_product = {line['product_id']: line['picked'] for line in reply['state']['lines']}
        self.assertEqual(by_product, {self.prod_a.id: 2.0, self.prod_b.id: 1.0})
        reply = svc.nc_barcode_adjust(picking.id, move_a.id, -5)               # more than was picked: back to zero
        picked = {line['product_id']: line['picked'] for line in reply['state']['lines']}
        self.assertEqual(picked[self.prod_a.id], 0.0)

    # ------------------------------------------------------------------ tracked products
    def test_a_tracked_product_asks_for_its_lot_and_the_lot_is_recorded(self):
        picking = self._picking('in', [(self.prod_lot, 4)])
        svc = self._svc(self.warehouse_user)
        svc.nc_barcode_open(picking.id)
        prompt = svc.nc_barcode_scan(picking.id, self.prod_lot.barcode)
        self.assertEqual(prompt['need_lot']['product_id'], self.prod_lot.id)
        self.assertEqual(prompt['state']['lines'][0]['picked'], 0.0)       # nothing counted until the lot is known
        reply = svc.nc_barcode_scan(picking.id, 'LOT-0001', qty=4, lot_for_product_id=self.prod_lot.id)
        self.assertTrue(reply['ok'], reply)
        self.assertEqual(reply['state']['lines'][0]['lots'], ['LOT-0001'])
        self.assertTrue(svc.nc_barcode_validate(picking.id)['done'])
        lot = self.env['stock.lot'].search([('name', '=', 'LOT-0001'), ('product_id', '=', self.prod_lot.id)])
        on_hand = self.env['stock.quant']._get_available_quantity(self.prod_lot, self.stock_loc, lot_id=lot)
        self.assertEqual(on_hand, 4.0)

    def test_an_unknown_lot_is_refused_on_a_delivery(self):
        self.env['stock.quant']._update_available_quantity(self.prod_lot, self.stock_loc, 5.0,
                                                           lot_id=self.env['stock.lot'].create(
                                                               {'name': 'LOT-REAL', 'product_id': self.prod_lot.id}))
        picking = self._picking('out', [(self.prod_lot, 2)])
        svc = self._svc(self.warehouse_user)
        svc.nc_barcode_open(picking.id)
        reply = svc.nc_barcode_scan(picking.id, 'LOT-NOPE', lot_for_product_id=self.prod_lot.id)
        self.assertFalse(reply['ok'])
        self.assertIn('Unknown lot', reply['message'])

    # ------------------------------------------------------------------ deliveries and locations
    def test_a_delivery_takes_the_scanned_quantity_out_of_stock(self):
        self.env['stock.quant']._update_available_quantity(self.prod_a, self.stock_loc, 10.0)
        picking = self._picking('out', [(self.prod_a, 3)])
        svc = self._svc(self.warehouse_user)
        svc.nc_barcode_open(picking.id)
        for _ in range(3):
            svc.nc_barcode_scan(picking.id, self.prod_a.barcode)
        self.assertTrue(svc.nc_barcode_validate(picking.id)['done'])
        self.assertEqual(self._stock(self.prod_a), 7.0)

    def test_a_location_must_belong_to_the_operation(self):
        picking = self._picking('int', [(self.prod_a, 1)])
        svc = self._svc(self.warehouse_user)
        svc.nc_barcode_open(picking.id)
        good = svc.nc_barcode_scan(picking.id, 'LOC-A1')
        self.assertEqual(good['location']['id'], self.shelf.id)
        bad = svc.nc_barcode_scan(picking.id, 'LOC-ELSE')
        self.assertFalse(bad['ok'])
        self.assertNotIn('location', bad)

    def test_the_destination_location_is_applied_to_the_scanned_line(self):
        picking = self._picking('in', [(self.prod_a, 1)])
        svc = self._svc(self.warehouse_user)
        svc.nc_barcode_open(picking.id)
        svc.nc_barcode_scan(picking.id, self.prod_a.barcode, location_id=self.shelf.id)
        self.assertEqual(picking.move_ids.move_line_ids.location_dest_id, self.shelf)

    # ------------------------------------------------------------------ the operations list
    def test_the_list_shows_open_operations_and_filters_by_kind(self):
        receipt = self._picking('in', [(self.prod_a, 1)])
        delivery = self._picking('out', [(self.prod_b, 1)])
        svc = self._svc(self.warehouse_user)
        names = {row['name'] for row in svc.nc_barcode_operations()}
        self.assertTrue({receipt.name, delivery.name} <= names)
        self.assertEqual({row['type'] for row in svc.nc_barcode_operations('incoming')}, {'incoming'})
        self.assertNotIn(delivery.name, {row['name'] for row in svc.nc_barcode_operations('incoming')})

    def test_scanning_another_operations_reference_switches_to_it(self):
        one, two = self._picking('in', [(self.prod_a, 1)]), self._picking('in', [(self.prod_b, 1)])
        svc = self._svc(self.warehouse_user)
        reply = svc.nc_barcode_scan(one.id, two.name)
        self.assertEqual(reply['switch_to'], two.id)

    # ------------------------------------------------------------------ rights and companies
    def test_a_user_without_stock_rights_cannot_scan_and_nothing_is_changed(self):
        picking = self._picking('in', [(self.prod_a, 1)])
        with self.assertRaises(AccessError):
            self._svc(self.employee).nc_barcode_scan(picking.id, self.prod_a.barcode)
        self.assertFalse(picking.move_ids.move_line_ids.filtered('picked'))

    def test_another_companys_operation_cannot_be_scanned(self):
        other = self.env['res.company'].create({'name': 'Other Barcode Co'})
        warehouse = self.env['stock.warehouse'].search([('company_id', '=', other.id)], limit=1)
        foreign = self.env['stock.picking'].with_company(other).create({
            'picking_type_id': warehouse.in_type_id.id, 'location_id': self.supplier.id,
            'location_dest_id': warehouse.lot_stock_id.id,
            'move_ids': [(0, 0, {'product_id': self.prod_a.id, 'product_uom_qty': 1,
                                 'product_uom': self.prod_a.uom_id.id,
                                 'location_id': self.supplier.id, 'location_dest_id': warehouse.lot_stock_id.id})]})
        with self.assertRaises(AccessError):
            self._svc(self.warehouse_user).nc_barcode_scan(foreign.id, self.prod_a.barcode)
        self.assertNotIn(foreign.name, {row['name'] for row in self._svc(self.warehouse_user).nc_barcode_operations()})

    def test_the_service_runs_as_the_caller_and_never_as_superuser(self):
        import inspect
        from odoo.addons.ncollection_stock_barcode.models import stock_barcode
        source = inspect.getsource(stock_barcode)
        self.assertNotIn('.sudo()', source)

    # ------------------------------------------------------------------ inventory count
    def test_a_count_is_scanned_and_applied_with_the_engines_inventory_adjustment(self):
        svc = self._svc(self.manager)
        opened = svc.nc_barcode_count_open('LOC-A1')
        self.assertEqual(opened['location']['id'], self.shelf.id)
        svc.nc_barcode_count_scan(self.shelf.id, self.prod_a.barcode)
        reply = svc.nc_barcode_count_scan(self.shelf.id, self.prod_a.barcode, qty=2)
        self.assertEqual(reply['rows'][0]['counted'], 3.0)
        applied = svc.nc_barcode_count_apply(self.shelf.id)
        self.assertTrue(applied['ok'])
        self.assertEqual(self._stock(self.prod_a, self.shelf), 3.0)

    def test_counting_needs_an_internal_location_and_a_product_barcode(self):
        svc = self._svc(self.manager)
        with self.assertRaises(UserError):
            svc.nc_barcode_count_open(self.customer.barcode or 'NO-SUCH-LOCATION')
        svc.nc_barcode_count_open('LOC-A1')
        reply = svc.nc_barcode_count_scan(self.shelf.id, 'NOT-A-PRODUCT')
        self.assertFalse(reply['ok'])

    def test_a_warehouse_user_counts_but_only_an_inventory_manager_applies(self):
        """Odoo's own inventory screen offers "Apply" to managers only; this screen keeps that rule. An employee with
        no stock rights can do neither, and nothing is written."""
        with self.assertRaises(AccessError):
            self._svc(self.employee).nc_barcode_count_scan(self.shelf.id, self.prod_b.barcode)
        self.assertEqual(self._stock(self.prod_b, self.shelf), 0.0)
        counter = self._svc(self.warehouse_user)
        counter.nc_barcode_count_scan(self.shelf.id, self.prod_b.barcode, qty=4)
        with self.assertRaises(AccessError):
            counter.nc_barcode_count_apply(self.shelf.id)
        self.assertEqual(self._stock(self.prod_b, self.shelf), 0.0)                 # the count is saved, not applied
        self.assertTrue(self._svc(self.manager).nc_barcode_count_apply(self.shelf.id)['ok'])
        self.assertEqual(self._stock(self.prod_b, self.shelf), 4.0)

    def test_quantities_must_be_finite_and_positive(self):
        picking = self._picking('in', [(self.prod_a, 2)])
        svc = self._svc(self.warehouse_user)
        svc.nc_barcode_open(picking.id)
        for bad in (0, -3, float('nan'), float('inf'), 'abc'):
            with self.assertRaises(UserError, msg=str(bad)):
                svc.nc_barcode_scan(picking.id, self.prod_a.barcode, qty=bad)
        with self.assertRaises(UserError):
            svc.nc_barcode_count_scan(self.shelf.id, self.prod_a.barcode, qty=float('nan'))

    def test_a_location_from_outside_the_operation_is_never_applied_to_a_line(self):
        picking = self._picking('in', [(self.prod_a, 1)])
        svc = self._svc(self.warehouse_user)
        svc.nc_barcode_open(picking.id)
        svc.nc_barcode_scan(picking.id, self.prod_a.barcode, location_id=self.elsewhere.id)
        self.assertEqual(picking.move_ids.move_line_ids.location_dest_id, self.stock_loc)

    def test_lookups_stay_inside_the_operations_company(self):
        other = self.env['res.company'].create({'name': 'Second Barcode Co'})
        self.manager.write({'company_ids': [(4, other.id)]})
        foreign = self.env['product.product'].with_company(other).create({
            'name': 'Foreign Widget', 'is_storable': True, 'barcode': 'FOREIGN-0001', 'company_id': other.id})
        picking = self._picking('in', [(self.prod_a, 1)])
        svc = self._svc(self.manager)
        svc.nc_barcode_open(picking.id)
        reply = svc.nc_barcode_scan(picking.id, foreign.barcode)
        self.assertFalse(reply['ok'])                                   # not matched to the other company's product

    # ------------------------------------------------------------------ the screen
    def test_the_screen_lives_inside_inventory_and_needs_stock_rights(self):
        menu = self.env.ref('ncollection_stock_barcode.menu_nc_barcode_scanner')
        self.assertEqual(menu.parent_id, self.env.ref('stock.menu_stock_warehouse_mgmt'))
        self.assertEqual(menu.group_ids, self.env.ref('stock.group_stock_user'))
        self.assertEqual(self.env.ref('ncollection_stock_barcode.action_nc_barcode_scanner').tag,
                         'ncollection_stock_barcode.scanner')
