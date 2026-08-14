# -*- coding: utf-8 -*-
"""Shared fixture for the P8-T05 suites."""
from odoo.tests import TransactionCase


class AuditCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Log = cls.env['auditlog.log']
        cls.Rule = cls.env['auditlog.rule']
        cls.Seal = cls.env['ncollection.audit.seal']
        # Seeding runs at install and on every registry load, so rules already
        # exist here. Asserting that rather than seeding again keeps the tests
        # measuring the shipped path instead of one they set up themselves.
        cls.Rule._nc_seed_rules()
        # A MODEL THE SUITE OWNS, audited explicitly for test purposes.
        #
        # The fixture used to generate audit rows through `res.partner`, which
        # broke the moment res.partner was withheld from the shipped rule set:
        # every tamper-evidence test failed at once because nothing produced a
        # log. That is a fixture depending on a product decision it has no
        # business depending on. `res.country` is in `base`, has no computed
        # churn and no group-restricted fields, so the suite generates rows on
        # any database — including one where only this module is installed.
        cls.probe_model = cls.env['ir.model']._get('res.country')
        if not cls.Rule.search_count([('model_id', '=', cls.probe_model.id)]):
            rule = cls.Rule.create({
                'name': 'NCollection audit TEST probe: res.country',
                'model_id': cls.probe_model.id,
                'log_write': True, 'log_create': True, 'log_unlink': True,
                'log_read': False, 'log_export_data': False,
                'log_type': 'full',
            })
            rule.set_to_confirmed()

    # ---- helpers ---------------------------------------------------------

    def _latest_log(self, model_name):
        model = self.env['ir.model']._get(model_name)
        log = self.Log.search([('model_id', '=', model.id)],
                              order='id desc', limit=1)
        self.assertTrue(log, "no audit log for %s — the rule is not active, so "
                             "whatever this test asserts next is vacuous"
                             % model_name)
        return log

    def _noise(self, name='Audit Noise'):
        """Produce audit rows on the suite's own probe model."""
        country = self.env['res.country'].create(
            {'name': name, 'code': self._probe_code()})
        country.name = "%s renamed" % name
        self.env.flush_all()
        return country

    _probe_seq = 0

    @classmethod
    def _probe_code(cls):
        """res.country.code is unique and two characters."""
        cls._probe_seq += 1
        return "Q%s" % chr(ord('A') + (cls._probe_seq % 26))

    def _invoice(self, price_unit=100.0):
        journal = self.env['account.journal'].search(
            [('type', '=', 'sale')], limit=1)
        account = self.env['account.account'].search(
            [('account_type', '=', 'income')], limit=1)
        partner = self.env['res.partner'].create({'name': 'Audit Customer'})
        move = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': partner.id,
            'journal_id': journal.id,
            'invoice_line_ids': [(0, 0, {
                'name': 'Widget', 'quantity': 1,
                'price_unit': price_unit, 'account_id': account.id})],
        })
        self.env.flush_all()
        return move
