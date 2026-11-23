"""The bank / cash card figures and actions. Every journal of type bank or cash gets them from its configuration;
nothing is keyed on a name, id, account, method or currency. Figures are read with the user's own rights, in two
grouped queries for all the cards on the dashboard (no query per card)."""
from datetime import timedelta

from odoo import fields, models
from odoo.exceptions import UserError
from odoo.tools.misc import format_date, get_lang

GRAPH_DAYS = 30


def res_sample(dashboard_data, journal):
    return dashboard_data.get(journal.id, {}).get('is_sample_data', False)


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
        history = self._nc_ops_journals()._nc_ledger_history()
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
                'nc_graph_empty': not history.get(journal.id),
                'is_sample_data': False if history.get(journal.id) else res_sample(dashboard_data, journal),
            })
        return res

    # -------------------------------------------------------------------- graph
    def _nc_ledger_history(self):
        """{journal_id: [(date, balance)] or []}: the POSTED ledger balance of each journal's account at the start of
        the window, after every day with a movement, and today, in the same currency rule as the Current Balance
        figure, so the last point IS that figure. [] when the account has never had a posted line (honest empty
        state: no sample, no invented history). One grouped query for all the cards."""
        journals = self._nc_ops_journals().filtered('default_account_id')
        today = fields.Date.context_today(self)
        start = today - timedelta(days=GRAPH_DAYS)
        result = {journal.id: [] for journal in self._nc_ops_journals()}
        if not journals:
            return result
        Line = self.env['account.move.line']
        domain = [('account_id', 'in', journals.default_account_id.ids), ('parent_state', '=', 'posted'),
                  ('company_id', 'in', self.env.companies.ids)]
        groups = Line._read_group(domain, groupby=['account_id', 'currency_id', 'date:day'],
                                  aggregates=['balance:sum', 'amount_currency:sum'])
        figures = journals._nc_ops_figures()
        for journal in journals:
            journal_currency = journal.currency_id
            company_currency = journal.company_id.currency_id
            per_day = {}
            any_line = False
            for account, currency, day, balance, amount_currency in groups:
                if account != journal.default_account_id:
                    continue
                any_line = True
                if journal_currency and currency == journal_currency:
                    value = amount_currency
                elif journal_currency and journal_currency != company_currency:
                    value = company_currency._convert(balance, journal_currency, journal.company_id)
                else:
                    value = balance
                per_day[day] = per_day.get(day, 0.0) + value
            if not any_line:
                continue
            opening = sum(value for day, value in per_day.items() if day < start)
            points = [(start, opening)]
            running = opening
            for day in sorted(d for d in per_day if start <= d <= today):
                running += per_day[day]
                points.append((day, running))
            running_total = figures[journal.id]['balance']
            if points[-1][0] != today:
                points.append((today, running_total))
            result[journal.id] = points
        return result

    def _get_bank_cash_graph_data(self):
        """Bank / cash cards plot the posted LEDGER balance (what Current Balance shows), not Odoo's statement balance
        and never its random sample data. Other journal types keep Odoo's graph."""
        result = super()._get_bank_cash_graph_data()
        history = self._nc_ledger_history()
        locale = get_lang(self.env).code
        for journal in self._nc_ops_journals():
            points = history.get(journal.id)
            if not points:
                continue
            currency = journal.currency_id or journal.company_id.currency_id
            values = [{'x': format_date(self.env, day, date_format='d MMM', lang_code=locale),
                       'name': format_date(self.env, day, date_format='d MMMM y', lang_code=locale),
                       'y': currency.round(amount)} for day, amount in points]
            result[journal.id] = [{'values': values, 'title': '', 'key': self.env._('Current Balance (posted)'),
                                   'area': True, 'color': '#2f6f8f', 'is_sample_data': False}]
        return result

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

    def action_nc_ops_posted_balance(self):
        """The movements behind the card's Current Balance: every transaction row that moved the journal's account,
        oldest balance first in the running total, newest first on screen. Its last running balance IS the Current
        Balance (same posted lines, same currency rule); a posted ledger balance, not a bank statement balance."""
        action = self.action_nc_ops_transactions()
        journal = self._nc_ops_journal()
        action['domain'] = [('journal_id', '=', journal.id), ('balance_amount', '!=', 0)]
        action['display_name'] = f'{journal.display_name} · {self.env._("Current Balance (posted)")}'
        action['help'] = self.env._('<p>No posted movement on this account yet.</p>')
        return action

    def action_nc_ops_pending(self):
        journal = self._nc_ops_journal()
        payments = self.env['account.move.line'].search(journal._nc_pending_domain()).payment_id
        action = self.env['ir.actions.act_window']._for_xml_id(
            'ncollection_journal_operations.action_pending_payments')
        action['domain'] = [('id', 'in', payments.ids), ('journal_id', '=', journal.id)]
        action['display_name'] = f'{journal.display_name} · {self.env._("Pending Balance")}'
        return action
