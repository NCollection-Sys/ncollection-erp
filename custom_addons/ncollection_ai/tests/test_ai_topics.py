# -*- coding: utf-8 -*-
import datetime

from odoo.tests import TransactionCase, tagged

from odoo.addons.ncollection_ai.models import ai_topics

_TODAY = datetime.date(2026, 11, 10)
_WRITE_WORDS = ('create', 'write', 'unlink', 'post', 'cancel', 'delete')


@tagged('post_install', '-at_install')
class TestAiTopics(TransactionCase):
    """The assistant's data topics: matched by keywords (English and Arabic), read-only, bounded, run as the user."""

    def test_english_questions_select_their_topics(self):
        cases = {
            "What do we owe our suppliers?": 'payables',
            "Which invoices are overdue?": 'overdue_receivables',
            "How are sales this year?": 'sales_by_month',
            "Show purchase orders by vendor": 'purchases_by_supplier',
            "How much did customers pay us?": 'customer_payments_by_month',
            "What is the stock on hand?": 'stock_on_hand',
        }
        for question, key in cases.items():
            self.assertIn(key, [s['key'] for s in ai_topics.select_specs(question, _TODAY)], question)

    def test_arabic_questions_select_their_topics(self):
        cases = {
            "ما هي فواتير الموردين المستحقة؟": 'payables',
            "كم المبيعات هذا الشهر؟": 'sales_by_month',
            "ما حالة المخزون؟": 'stock_on_hand',
            "أين الفواتير المتأخرة؟": 'overdue_receivables',
        }
        for question, key in cases.items():
            self.assertIn(key, [s['key'] for s in ai_topics.select_specs(question, _TODAY)], question)

    def test_an_unrelated_question_selects_nothing_and_the_number_of_topics_is_capped(self):
        self.assertEqual(ai_topics.select_specs("hello there", _TODAY), [])
        many = "sales purchases payments stock payables overdue"
        keys = {s['key'] for s in ai_topics.select_specs(many, _TODAY)}
        self.assertLessEqual(len(keys), 3)

    def test_every_spec_is_a_bounded_read_group_and_nothing_else(self):
        allowed_keys = {'key', 'model', 'domain', 'groupby', 'aggregates', 'limit', 'order'}
        for _key, _en, _ar, factory in ai_topics.TOPICS:
            for spec in factory(_TODAY):
                self.assertEqual(set(spec), allowed_keys, spec['key'])
                self.assertTrue(0 < spec['limit'] <= 12, spec['key'])
                self.assertTrue(all(':sum' in a for a in spec['aggregates']))
                for word in _WRITE_WORDS:
                    self.assertNotIn(word, spec['model'])

    def test_the_specs_run_through_the_engine_as_the_asking_user_and_a_missing_model_is_just_absent(self):
        context = self.env['ncollection.ai.context']._build(
            specs=ai_topics.select_specs("payables overdue inventory sales", _TODAY), max_tokens=4000)
        self.assertIn('workspace', context['sections'])
        # a topic whose app is not installed or not readable is simply not there; nothing raised
        for key in context['sections']:
            self.assertIn(key, {s['key'] for _k, _e, _a, f in ai_topics.TOPICS for s in f(_TODAY)} | {
                'workspace', 'receivables', 'recent_invoices', 'top_customers'})

    def test_a_user_without_the_financial_gate_cannot_use_the_assistant_at_all(self):
        plain = self.env['res.users'].create({
            'name': 'ai_plain_topics', 'login': 'ai_plain_topics', 'password': 'Ai-Plain-Pass1!',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        with self.assertRaises(Exception):
            self.env['ncollection.ai.question'].with_user(plain).ask("what do we owe suppliers")


@tagged('post_install', '-at_install')
class TestNewTopicsAndReadiness(TransactionCase):

    def test_revenue_is_invoices_not_orders_or_receipts(self):
        keys = [s['key'] for s in ai_topics.select_specs("What was our revenue last quarter?", _TODAY)]
        self.assertIn('revenue_by_month', keys)
        self.assertNotIn('sales_by_month', keys)
        self.assertNotIn('customer_payments_by_month', keys)
        spec = next(s for s in ai_topics.select_specs("revenue", _TODAY) if s['key'] == 'revenue_by_month')
        self.assertEqual(spec['model'], 'account.move')
        self.assertIn(('state', '=', 'posted'), spec['domain'])

    def test_each_new_topic_in_both_languages(self):
        cases = {"What is our bank balance?": 'bank_cash_balances', "كم رصيد البنك؟": 'bank_cash_balances',
                 "How many bank lines are left to reconcile?": 'bank_lines_to_reconcile',
                 "ما هي المصروفات هذا العام؟": 'supplier_bills_by_month',
                 "Show supplier bills": 'supplier_bills_by_month',
                 "What are the top products?": 'top_products_12_months', "أفضل المنتجات": 'top_products_12_months'}
        for question, key in cases.items():
            self.assertIn(key, [s['key'] for s in ai_topics.select_specs(question, _TODAY)], question)

    def test_every_topic_is_a_read_group_and_never_a_write(self):
        for _key, _en, _ar, factory in ai_topics.TOPICS:
            for spec in factory(_TODAY):
                self.assertEqual(set(spec) - {'key', 'model', 'domain', 'groupby', 'aggregates', 'limit', 'order'},
                                 set(), spec['key'])
                self.assertLessEqual(spec['limit'], 12)

    def test_readiness_without_a_gateway_is_unavailable_and_admin_only(self):
        from odoo.exceptions import AccessError, UserError
        from odoo.tests import new_test_user
        admin = new_test_user(self.env, login='nc_ai_admin',
                              groups='base.group_user,ncollection_core.group_tenant_admin')
        plain = new_test_user(self.env, login='nc_ai_plain', groups='base.group_user')
        self.env['ir.config_parameter'].set_param('ncollection_ai.gateway_url', 'http://127.0.0.1:9')
        Readiness = self.env['ncollection.ai.readiness']
        state = Readiness.with_user(admin)._nc_probe()
        self.assertEqual((state['reachable'], state['authenticated'], state['mode']), (False, False, 'unavailable'))
        with self.assertRaises(AccessError):
            Readiness.with_user(plain).action_open()
        record = Readiness.with_user(admin).create(state)
        with self.assertRaises(UserError):
            record.action_set_free_text()
        self.assertFalse(self.env['ncollection.ai.question']._free_text_enabled())
