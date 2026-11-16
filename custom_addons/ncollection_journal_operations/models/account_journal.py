"""The bank / cash card figures and actions. Every journal of type bank or cash gets them from its configuration;
nothing is keyed on a name, id, account, method or currency. Figures are read with the user's own rights, in two
grouped queries for all the cards on the dashboard (no query per card)."""
from odoo import models
from odoo.exceptions import UserError


class AccountJournal(models.Model):
    _inherit = 'account.journal'

    # ------------------------------------------------------------------ figures
    def _nc_ops_journals(self):
        return self.filtered(lambda journal: journal.type in ('bank', 'cash'))

    def _nc_pending_domain(self):
        """Posted, in-process payments of these journals whose liquidity line still waits on an account that is not
        the journal's own liquidity account. Shared by the card figure and the drill-down list."""
        return [
            ('payment_id.journal_id', 'in', self.ids),
            ('payment_id.state', 'in', ('in_process', 'paid')),
            ('parent_state', '=', 'posted'),
            ('company_id', 'in', self.env.companies.ids),
            ('account_id.reconcile', '=', True),
            ('reconciled', '=', False),
            ('account_id', 'in', self._nc_outstanding_accounts().ids),
        ]

    def _nc_outstanding_accounts(self):
        lines = self.inbound_payment_method_line_ids | self.outbound_payment_method_line_ids
        # The account a POSTED payment actually used may differ from today's method configuration: take both.
        posted = self.env['account.move.line']._read_group(
            [('payment_id.journal_id', 'in', self.ids), ('payment_id.state', 'in', ('in_process', 'paid')),
             ('parent_state', '=', 'posted'), ('account_id.reconcile', '=', True),
             ('account_id.account_type', 'not in', ('asset_receivable', 'liability_payable'))],
            groupby=['account_id'])
        posted_accounts = self.env['account.account'].union(*[account for (account,) in posted])
        companies = self.company_id
        transfer = companies.transfer_account_id
        return ((lines.payment_account_id | posted_accounts) - self.default_account_id - transfer) \
            .filtered(lambda account: account.reconcile)

    def _nc_ops_figures(self):
        """{journal_id: {...}} for the cards: ledger balance and pending in / out, in the journal currency when every
        line is in it, otherwise in company currency (``approx``)."""
        journals = self._nc_ops_journals()
        result = {j.id: {'balance': 0.0, 'balance_approx': False, 'pending_in': 0.0, 'pending_out': 0.0,
                         'pending_count': 0, 'pending_approx': False} for j in journals}
        if not journals:
            return result
        Line = self.env['account.move.line']
        companies = self.env.companies.ids
        by_account = {}
        for journal in journals:
            if journal.default_account_id:
                by_account.setdefault(journal.default_account_id.id, []).append(journal)
        if by_account:
            groups = Line._read_group(
                [('account_id', 'in', list(by_account)), ('parent_state', '=', 'posted'),
                 ('company_id', 'in', companies)],
                groupby=['account_id', 'currency_id'], aggregates=['balance:sum', 'amount_currency:sum'])
            for account, currency, balance, amount_currency in groups:
                for journal in by_account.get(account.id, []):
                    data = result[journal.id]
                    journal_currency = journal.currency_id
                    if journal_currency and currency == journal_currency:
                        data['balance'] += amount_currency
                    elif journal_currency and journal_currency != journal.company_id.currency_id:
                        data['balance'] += journal.company_id.currency_id._convert(
                            balance, journal_currency, journal.company_id)
                        data['balance_approx'] = True
                    else:
                        data['balance'] += balance
        # Incoming and outgoing apart (one query each), so a receipt and a payment are never netted inside a group.
        pending = []
        for sign in ('>', '<'):
            pending += Line._read_group(
                journals._nc_pending_domain() + [('amount_residual', sign, 0)],
                groupby=['journal_id', 'currency_id'],
                aggregates=['amount_residual:sum', 'amount_residual_currency:sum', 'payment_id:count_distinct'])
        for journal, currency, residual, residual_currency, count in pending:
            data = result.get(journal.id)
            if data is None:
                continue
            journal_currency = journal.currency_id or journal.company_id.currency_id
            value = residual_currency if currency == journal_currency else residual
            if currency != journal_currency and journal_currency != journal.company_id.currency_id:
                value = journal.company_id.currency_id._convert(residual, journal_currency, journal.company_id)
            if currency != journal_currency:
                data['pending_approx'] = True
            data['pending_in' if value > 0 else 'pending_out'] += value
            data['pending_count'] += count
        return result

    def _fill_bank_cash_dashboard_data(self, dashboard_data):
        res = super()._fill_bank_cash_dashboard_data(dashboard_data)
        figures = self._nc_ops_journals()._nc_ops_figures()
        for journal in self._nc_ops_journals():
            data = figures[journal.id]
            currency = journal.currency_id or journal.company_id.sudo().currency_id
            net = data['pending_in'] + data['pending_out']
            dashboard_data[journal.id].update({
                'nc_ops': True,
                'nc_balance': currency.format(data['balance']),
                'nc_balance_approx': data['balance_approx'],
                'nc_balance_negative': currency.compare_amounts(data['balance'], 0.0) < 0,
                'nc_pending': currency.format(net),
                'nc_pending_in': currency.format(data['pending_in']),
                'nc_pending_out': currency.format(abs(data['pending_out'])),
                'nc_pending_count': data['pending_count'],
                'nc_pending_approx': data['pending_approx'],
            })
        return res

    # ------------------------------------------------------------------ actions
    def _nc_ops_journal(self):
        self.ensure_one()
        if self.type not in ('bank', 'cash'):
            raise UserError(self.env._("%(journal)s is not a bank or cash journal.", journal=self.display_name))
        self.check_access('read')
        return self

    def _nc_new_operation(self, operation):
        journal = self._nc_ops_journal()
        self.env['account.payment'].check_access('create')
        view = 'ncollection_journal_operations.view_payment_form_operation'
        name = self.env._('Payment') if operation == 'payment' else self.env._('Receipt')
        return {
            'type': 'ir.actions.act_window',
            'name': f'{journal.display_name} · {name}',
            'res_model': 'account.payment',
            'view_mode': 'form',
            'views': [(self.env.ref(view).id, 'form')],
            'target': 'current',
            'context': {
                'default_nc_operation': operation,
                'default_nc_source_journal_id': journal.id,
                'default_journal_id': journal.id,
                'default_payment_type': 'outbound' if operation == 'payment' else 'inbound',
                'default_nc_counterpart_type': 'supplier' if operation == 'payment' else 'customer',
                'default_partner_type': 'supplier' if operation == 'payment' else 'customer',
            },
        }

    def action_nc_ops_payment(self):
        return self._nc_new_operation('payment')

    def action_nc_ops_receipt(self):
        return self._nc_new_operation('receipt')

    def action_nc_ops_transfer(self):
        journal = self._nc_ops_journal()
        self.env['ncollection.internal.transfer'].check_access('create')
        return {
            'type': 'ir.actions.act_window',
            'name': f'{journal.display_name} · {self.env._("Internal Transfer")}',
            'res_model': 'ncollection.internal.transfer',
            'view_mode': 'form',
            'target': 'current',
            'context': {'default_source_journal_id': journal.id},
        }

    def action_nc_ops_reconcile(self):
        """The existing matching workspace (account_reconcile_oca), on this journal only."""
        return self._nc_ops_journal().action_nc_bank_to_reconcile()

    def action_nc_ops_transactions(self):
        journal = self._nc_ops_journal()
        action = self.env['ir.actions.act_window']._for_xml_id(
            'ncollection_journal_operations.action_journal_transaction')
        action['domain'] = [('journal_id', '=', journal.id)]
        action['context'] = {'nc_journal_id': journal.id}
        action['display_name'] = f'{journal.display_name} · {self.env._("Transactions")}'
        return action

    def action_nc_ops_pending(self):
        journal = self._nc_ops_journal()
        payments = self.env['account.move.line'].search(journal._nc_pending_domain()).payment_id
        action = self.env['ir.actions.act_window']._for_xml_id(
            'ncollection_journal_operations.action_pending_payments')
        action['domain'] = [('id', 'in', payments.ids), ('journal_id', '=', journal.id)]
        action['display_name'] = f'{journal.display_name} · {self.env._("Pending Balance")}'
        return action
