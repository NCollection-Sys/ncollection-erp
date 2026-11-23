# -*- coding: utf-8 -*-
"""Request 09.2: operational lists open newest first with a deterministic tie-break. Display order only: running
balances are computed oldest first on their own key (journal transactions, ledger viewer)."""
from lxml import etree

from odoo.tests import TransactionCase, tagged

# model -> the leading keys its list must sort on, newest first (documented in FIN_UX_14 / JOURNAL_OPERATIONS.md)
EXPECTED = {
    'account.move': ('date desc', 'id desc'),             # invoices, bills, entries: accounting date, then entry
    'account.payment': ('date desc', 'id desc'),          # payments / receipts: payment date, then entry
    'ncollection.journal.transaction': ('value_date desc', 'id desc'),
    'ncollection.internal.transfer': ('date desc', 'id desc'),
}


@tagged('post_install', '-at_install')
class TestNewestFirst(TransactionCase):

    def _order(self, model):
        if model not in self.env:
            return None
        arch = self.env[model].get_view(view_type='list')['arch']
        return etree.fromstring(arch).get('default_order') or self.env[model]._order

    def test_every_operational_list_is_newest_first_with_a_tie_break(self):
        for model, (lead, tie) in EXPECTED.items():
            order = self._order(model)
            if order is None:
                continue
            with self.subTest(model=model):
                keys = [k.strip() for k in order.split(',')]
                self.assertEqual(keys[0], lead, order)
                self.assertIn(tie, keys, order)

    def test_master_data_stays_alphabetical(self):
        self.assertTrue(self.env['res.partner']._order.startswith(('complete_name', 'display_name', 'name')))
