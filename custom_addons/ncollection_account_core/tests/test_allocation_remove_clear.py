# -*- coding: utf-8 -*-
"""Request 05: Remove one allocation, Clear All, and never re-apply what the user took out. Total = Allocated +
Unallocated before and after Confirm, read from Odoo's own reconciliation."""
from odoo.exceptions import UserError
from odoo.tests import Form, tagged

from .test_payment_workflow import PAST, FUTURE, _Common


@tagged('post_install', '-at_install')
class TestAllocationRemoveClear(_Common):

    def _form(self, amount, partner=None):
        form = Form(self.env['account.payment'].with_context(default_payment_type='inbound',
                                                             default_partner_type='customer'))
        form.partner_id = partner or self.customer
        form.journal_id = self.bank
        form.amount = amount
        return form

    def test_the_form_seeds_the_automatic_plan(self):
        old = self._doc('out_invoice', 50.0, due=PAST)
        new = self._doc('out_invoice', 80.0, due=FUTURE)
        form = self._form(100.0)
        self.assertEqual(set(form.nc_selected_move_ids.ids), {old.id, new.id})
        self.assertFalse(form.nc_alloc_locked)

    def test_remove_one_is_never_reapplied_and_the_rest_stays(self):
        old = self._doc('out_invoice', 50.0, due=PAST)
        new = self._doc('out_invoice', 80.0, due=FUTURE)
        form = self._form(100.0)
        form.nc_selected_move_ids.remove(id=old.id)
        self.assertTrue(form.nc_alloc_locked)
        form.amount = 120.0                               # a recalculation: the removed invoice must not come back
        self.assertEqual(form.nc_selected_move_ids.ids, [new.id])
        payment = form.save()
        payment.action_nc_post_and_allocate()
        self.assertEqual(old.amount_residual, 50.0, 'removed: untouched')
        self.assertEqual(new.payment_state, 'paid')
        self.assertAlmostEqual(payment.nc_unallocated_amount, 40.0)
        self.assertAlmostEqual(80.0 + payment.nc_unallocated_amount, payment.amount, msg='total = allocated + left')
        self.assertTrue(old.exists() and payment.exists())

    def test_clear_all_leaves_everything_unallocated(self):
        inv = self._doc('out_invoice', 50.0)
        payment = self._form(30.0).save()
        payment.action_nc_clear_allocations()
        self.assertFalse(payment.nc_selected_move_ids)
        payment.amount = 35.0                              # still nothing comes back
        payment.action_nc_post_and_allocate()
        self.assertEqual(inv.amount_residual, 50.0)
        self.assertAlmostEqual(payment.nc_unallocated_amount, 35.0)
        self.assertEqual(payment.state, 'in_process')

    def test_automatic_restores_the_plan_and_partial_allocation_works(self):
        a = self._doc('out_invoice', 50.0, due=PAST)
        b = self._doc('out_invoice', 80.0, due=FUTURE)
        payment = self._form(70.0).save()
        payment.action_nc_clear_allocations()
        payment.action_nc_auto_allocate()
        self.assertEqual(set(payment.nc_selected_move_ids.ids), {a.id, b.id})
        payment.action_nc_post_and_allocate()
        self.assertEqual(a.payment_state, 'paid')
        self.assertEqual((b.payment_state, b.amount_residual), ('partial', 60.0))

    def test_confirmed_payments_cannot_be_reallocated_and_are_never_applied_twice(self):
        self._doc('out_invoice', 50.0)
        payment = self._form(50.0).save()
        payment.action_nc_post_and_allocate()
        with self.assertRaises(UserError):
            payment.action_nc_clear_allocations()
        with self.assertRaises(UserError):
            payment.action_nc_post_and_allocate()

    def test_another_partners_document_is_refused(self):
        other = self.env['res.partner'].create({'name': 'Someone Else'})
        foreign = self._doc('out_invoice', 40.0, partner=other)
        payment = self._form(40.0).save()
        payment.write({'nc_selected_move_ids': [(4, foreign.id)], 'nc_alloc_locked': True})
        with self.assertRaises(UserError):
            payment.action_nc_post_and_allocate()
        self.assertEqual(foreign.amount_residual, 40.0)

    def test_a_payment_not_made_on_the_form_keeps_the_automatic_behaviour(self):
        inv = self._doc('out_invoice', 25.0)
        payment = self._payment(25.0, self.customer)
        self.assertFalse(payment.nc_alloc_seed)
        payment.action_nc_post_and_allocate()
        self.assertEqual(inv.payment_state, 'paid')
