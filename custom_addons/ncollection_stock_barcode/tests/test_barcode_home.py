# -*- coding: utf-8 -*-
"""Barcode is a real application on Home: its own tile, the scanner behind it, licensed and authorised like Inventory.

The tile is a root menu that opens the scanner itself. Three independent gates, each tested alone so a pass cannot be
borrowed from another: rights (stock users), the plan (Ring 1 follows ``stock`` through NATIVE_FOLLOWS_LICENCE) and
the ORM (Ring 2 refuses the stock models whatever a menu says, because a visible tile grants nothing).
"""
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

SVC = 'ncollection.stock.barcode'
TILE = 'ncollection_stock_barcode.menu_nc_barcode_root'


@tagged('post_install', '-at_install')
class TestBarcodeHome(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Menu = cls.env['ir.ui.menu']
        cls.Config = cls.env['ncollection.workspace.config']
        cls.warehouse_user = cls._user('bch_wh', 'stock.group_stock_user')
        cls.employee = cls._user('bch_emp', 'base.group_user')

    @classmethod
    def _user(cls, login, group):
        return cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': login, 'login': login,
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, cls.env.ref(group).id])]})

    def _picking(self):
        warehouse = self.env['stock.warehouse'].search([], limit=1)
        supplier = self.env.ref('stock.stock_location_suppliers')
        product = self.env['product.product'].create({'name': 'Home Widget', 'is_storable': True})
        picking = self.env['stock.picking'].create({
            'picking_type_id': warehouse.in_type_id.id, 'location_id': supplier.id,
            'location_dest_id': warehouse.lot_stock_id.id,
            'move_ids': [(0, 0, {'product_id': product.id, 'product_uom_qty': 1, 'product_uom': product.uom_id.id,
                                 'location_id': supplier.id, 'location_dest_id': warehouse.lot_stock_id.id})]})
        picking.action_confirm()
        return picking

    def _tiles(self, user):
        return {a['xmlid']: a for a in self.Menu.with_user(user).nc_tenant_apps()}

    def _plan(self, modules):
        self.Config.search([]).unlink()
        self.Config.create({'plan_code': 'T', 'allowed_module_names': modules})
        self.env.registry.clear_cache()

    # ------------------------------------------------------------------ the application itself
    def test_it_is_a_root_menu_that_opens_the_scanner_with_the_module_icon(self):
        root = self.env.ref(TILE)
        self.assertFalse(root.parent_id)
        self.assertEqual(root.action, self.env.ref('ncollection_stock_barcode.action_nc_barcode_scanner'))
        self.assertEqual(root.web_icon, 'ncollection_stock_barcode,static/description/icon.png')
        self.assertTrue(root.web_icon_data, "the NCollection-convention icon is loaded for the tile")
        self.assertEqual(root.group_ids, self.env.ref('stock.group_stock_user'))

    def test_the_inventory_entry_is_kept(self):
        self.assertTrue(self.env.ref('ncollection_stock_barcode.menu_nc_barcode_scanner').parent_id)

    # ------------------------------------------------------------------ who sees the tile
    def test_a_warehouse_user_gets_the_tile_when_the_plan_includes_inventory(self):
        self._plan('stock,sale')
        tile = self._tiles(self.warehouse_user).get(TILE)
        self.assertTrue(tile, "licensed (stock) and authorised (stock user): the tile is on Home")
        self.assertEqual(tile['name'], 'Barcode')
        self.assertTrue(tile['web_icon_data'])

    def test_the_tile_follows_the_plan_not_its_own_name(self):
        self._plan('crm,sale')                                  # a plan WITHOUT Inventory
        self.assertNotIn(TILE, self._tiles(self.warehouse_user))

    def test_a_user_without_inventory_rights_never_gets_the_tile(self):
        self._plan('stock,sale')
        self.assertNotIn(TILE, self._tiles(self.employee))

    def test_the_owner_hiding_inventory_on_home_hides_its_front_door_too(self):
        self._plan('stock,sale')
        self.env['ncollection.launcher.app']._nc_sync()
        row = self.env['ncollection.launcher.app'].sudo().search([('module_name', '=', 'stock')])
        row.write({'visible': False})
        self.assertNotIn(TILE, self._tiles(self.warehouse_user))
        row.write({'visible': True})
        self.assertIn(TILE, self._tiles(self.warehouse_user))

    # ------------------------------------------------------------------ the tile grants nothing
    def test_without_the_licence_the_scanner_is_refused_by_the_orm_even_by_direct_call(self):
        """No tile is not a control. A warehouse user on a plan without Inventory calls the scanner's own methods
        directly: the stock models (Ring 2) refuse, whatever any menu says."""
        picking = self._picking()
        self._plan('crm,sale')
        with self.assertRaises(AccessError):
            self.env[SVC].with_user(self.warehouse_user).nc_barcode_open(picking.id)

    def test_a_user_without_inventory_rights_is_refused_by_direct_call(self):
        picking = self._picking()
        self._plan('stock,sale')
        with self.assertRaises(AccessError):
            self.env[SVC].with_user(self.employee).nc_barcode_open(picking.id)

    def test_the_scanner_opens_for_an_authorised_licensed_user(self):
        self._plan('stock,sale')
        picking = self._picking()
        reply = self.env[SVC].with_user(self.warehouse_user).nc_barcode_open(picking.id)
        self.assertTrue(reply['ok'], reply)
