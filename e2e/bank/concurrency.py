# Two REAL database sessions racing for one bank transaction (scenario 16). Run in `odoo shell` against a
# DISPOSABLE database only (it commits its fixture): see e2e/bank/README.md.
#   session A: locks the transaction row (as a user's Validate would, mid-flight)
#   session B: tries to reconcile the same transaction -> must be refused at once, nothing booked
#   then A releases, B retries -> booked exactly once; a third attempt -> refused (already reconciled)
from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import Form

V = 'account_reconcile_oca.bank_statement_line_form_reconcile_view'
env['account.reconcile.model'].search([('trigger', '=', 'auto_reconcile')]).active = False
company = env.company
journal = env['account.journal'].search([('code', '=', 'CCBM')], limit=1) or env['account.journal'].create(
    {'name': 'Bank Misr (concurrency)', 'type': 'bank', 'code': 'CCBM'})
journal.suspense_account_id = company.account_journal_suspense_account_id
partner = env['res.partner'].create({'name': 'Concurrency customer'})
inv = env['account.move'].create({'move_type': 'out_invoice', 'partner_id': partner.id, 'invoice_date': '2026-03-10',
                                  'invoice_line_ids': [Command.create({'name': 'S', 'quantity': 1, 'price_unit': 750,
                                                                       'tax_ids': [Command.clear()]})]})
inv.action_post()
item = inv.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable')
st = env['account.bank.statement.line'].create({'journal_id': journal.id, 'amount': 750, 'payment_ref': 'Race',
                                                'date': '2026-03-10', 'partner_id': partner.id})
env['ir.ui.view'].search([('name', '=', 'concurrency: visible')]).unlink()
env['ir.ui.view'].create({'name': 'concurrency: visible', 'model': 'account.bank.statement.line',
                          'inherit_id': env.ref(V).id, 'arch': '<data><field name="manual_reference" position="attributes">'
                          '<attribute name="invisible">0</attribute></field></data>'})
with Form(st, view=V) as f:
    f.add_account_move_line_id = item
env.cr.commit()

other = env.registry.cursor()                                  # session A
other.execute('SELECT id FROM account_bank_statement_line WHERE id = %s FOR UPDATE', [st.id])
try:
    st.reconcile_bank_line()                                   # session B
    print('CONCURRENCY FAIL: reconciled while another session held the lock')
except UserError as exc:
    print('CONCURRENCY OK refused while locked:', str(exc)[:90])
env.cr.rollback()
print('CONCURRENCY residual while locked', inv.amount_residual, 'reconciled', st.is_reconciled)
other.rollback()
other.close()                                                  # A releases

st.invalidate_recordset()
st.reconcile_bank_line()
env.cr.commit()
inv.invalidate_recordset()
print('CONCURRENCY after retry: residual', inv.amount_residual, 'reconciled', st.is_reconciled)
try:
    st.reconcile_bank_line()
    print('CONCURRENCY FAIL: reconciled twice')
except UserError:
    print('CONCURRENCY OK second attempt refused')
env.cr.rollback()
moves = env['account.move.line'].search_count([('statement_line_id', '=', st.id)])
print('CONCURRENCY journal items on the transaction', moves, 'balanced', abs(sum(st.move_id.line_ids.mapped('balance'))) < 0.005)
