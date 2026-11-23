# -*- coding: utf-8 -*-
"""Request 09.2: one company setting colours every financial table; tables use the semantic classes, never a
hard-coded colour, and directions keep their labels."""
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestFinancialPalette(TransactionCase):

    def test_default_and_choices(self):
        self.assertEqual(self.env.company.nc_fin_palette, 'blue_red')
        self.env.company.nc_fin_palette = 'blue_orange'
        self.assertEqual(self.env['res.config.settings'].create({}).nc_fin_palette, 'blue_orange')
