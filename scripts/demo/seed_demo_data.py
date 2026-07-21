# -*- coding: utf-8 -*-
"""Demo-tenant seed (INFRA-07) — run via `odoo shell -d <tenant_db>`.

Follows the same isolation contract as
custom_addons/ncollection_saas/scripts/provisioning/seed_tenant.py: this executes
inside an `odoo shell` subprocess bound to ONE tenant database, so it is never a
cross-database ORM call (Standing Rule 3). `env` is the shell global.

WHY THIS EXISTS
    The P2-T01 provisioning engine deliberately passes `--without-demo=True`,
    which is correct — a paying customer must never receive Odoo's demo data.
    That leaves a freshly provisioned tenant completely empty, so every dashboard
    KPI reads 0 and both charts draw empty axes. This seeds curated GCC data so
    the product can actually be looked at, and so the P1-T17 charts are finally
    validated against real numbers instead of empty series.

WHAT IT WRITES, AND WHY EACH PIECE
    Every record here exists because a specific dashboard widget queries it.
    Removing any one of them silently returns that tile to zero:

      sales_this_month (+trend) : confirmed sale.orders in THIS and LAST month
      top_customers             : confirmed orders across >= 5 partners
      receivables               : POSTED customer invoices left unpaid
      payables                  : POSTED vendor bills left unpaid
      revenue_6m                : posted income lines spread over 6 months
      cash_bank                 : a posted entry on the bank journal
      open_activities           : mail.activity spread across several assignees
                                  (they are per-user, so putting them all on
                                  admin leaves every other role showing 0)

    Content mirrors demo/src/mock/data.ts so the real product matches the
    prototype that was signed off.

IDEMPOTENT: re-running is a no-op unless SEED_FORCE=1 is set.
"""
import os
from datetime import date

from dateutil.relativedelta import relativedelta

env = env  # noqa: F821 - provided by the odoo shell runtime

FORCE = os.environ.get('SEED_FORCE') == '1'
ADMIN_PW = os.environ.get('DEMO_ADMIN_PASSWORD', 'demo1234')

# Mirrors demo/src/mock/data.ts (customer, amount) — real GCC names.
CUSTOMERS = [
    ('Emaar Properties', 84500),
    ('Majid Al Futtaim', 52300),
    ('Nakheel', 127400),
    ('Al-Futtaim Group', 46200),
    ('DAMAC', 31900),
    ('Aldar Properties', 18750),
]
VENDORS = [('Gulf Building Supplies', 38400), ('Emirates Logistics', 12250)]

# name, login, NCollection role xml-id (P1-T08). Mirrors the prototype's users.
STAFF = [
    ('Layla Al Nuaimi', 'layla@albarari.ae', 'group_role_owner'),
    ('Omar Haddad', 'omar@albarari.ae', 'group_role_ceo'),
    ('Fatima Rahmani', 'fatima@albarari.ae', 'group_role_accountant'),
    ('Yousef Karim', 'yousef@albarari.ae', 'group_role_sales'),
    ('Sara Mansour', 'sara@albarari.ae', 'group_role_manager'),
    ('Bilal Ahmed', 'bilal@albarari.ae', 'group_role_warehouse'),
    ('Noura Saleh', 'noura@albarari.ae', 'group_role_hr'),
    ('Aisha Darwish', 'aisha@albarari.ae', 'group_role_employee'),
]

company = env.company
today = date.today()

if env['res.partner'].search_count([('customer_rank', '>', 0)]) and not FORCE:
    print('SEED: already populated — nothing to do (set SEED_FORCE=1 to re-seed)')
else:
    # -- 1. Currency ---------------------------------------------------------
    # Must happen BEFORE any accounting entry exists: Odoo resists changing a
    # company's currency once journal items reference it.
    aed = env.ref('base.AED', raise_if_not_found=False)
    if aed:
        if not aed.active:
            aed.active = True
        if company.currency_id != aed:
            company.currency_id = aed
    print('SEED: currency =', company.currency_id.name)

    # -- 2. A password you can actually log in with --------------------------
    # seed_tenant.py forces a reset on purpose (a real tenant must have no known
    # password). A demo tenant is meant to be opened, so give it one.
    admin = env.ref('base.user_admin')
    admin.write({'password': ADMIN_PW})

    # -- 3. Link role -> Odoo app groups ------------------------------------
    # MUST run before creating users. ncollection_core's post_init_hook links
    # these, but it fires during install and deliberately skips modules that do
    # not exist yet — and provisioning installs core alongside sale/account in a
    # single `-i`. So on a freshly provisioned tenant the Accountant role grants
    # the 'financial' widget group but NOT account.group_account_user, and the
    # user lands on an empty dashboard. hooks.py states the contract: "re-run
    # after any module install". See INFRA-07 notes — the provisioning engine
    # not re-running this is a real gap, filed separately.
    from odoo.addons.ncollection_core.hooks import _sync_role_implications
    synced = _sync_role_implications(env)
    linked = sum(len(d['linked']) for d in synced.values())
    print('SEED: role implications linked =', linked)

    # -- 4. Staff across all 8 roles ----------------------------------------
    base_user = env.ref('base.group_user')
    for name, login, role in STAFF:
        if env['res.users'].search_count([('login', '=', login)]):
            continue
        grp = env.ref('ncollection_core.%s' % role, raise_if_not_found=False)
        gids = [base_user.id] + ([grp.id] if grp else [])
        env['res.users'].create({
            'name': name, 'login': login, 'password': ADMIN_PW,
            'group_ids': [(6, 0, gids)],
        })
    print('SEED: staff users =', env['res.users'].search_count([]))
    salesperson = env['res.users'].search([('login', '=', 'yousef@albarari.ae')], limit=1)

    # -- 5. Partners ---------------------------------------------------------
    Partner = env['res.partner']

    def _partner(name, customer=True):
        rec = Partner.search([('name', '=', name)], limit=1)
        if not rec:
            rec = Partner.create({
                'name': name, 'company_type': 'company', 'country_id':
                env.ref('base.ae').id,
                'customer_rank': 1 if customer else 0,
                'supplier_rank': 0 if customer else 1,
            })
        return rec

    customers = [(_partner(n), amt) for n, amt in CUSTOMERS]
    vendors = [(_partner(n, customer=False), amt) for n, amt in VENDORS]

    # -- 6. A product to put on the lines ------------------------------------
    # A fresh tenant has none, and both sale.order and account.move need one.
    product = env['product.product'].search([('name', '=', 'Consulting Services')], limit=1)
    if not product:
        product = env['product.product'].create({
            'name': 'Consulting Services', 'type': 'service',
            'list_price': 1000.0, 'invoice_policy': 'order',
        })

    # -- 7. Confirmed sales: this month AND last month (feeds the trend) -----
    Order = env['sale.order']
    this_month = today.replace(day=1) + relativedelta(days=3)
    last_month = (today.replace(day=1) - relativedelta(months=1)) + relativedelta(days=3)
    made = 0
    for idx, (partner, amount) in enumerate(customers):
        # Alternate months so "this vs last" has something real to compare.
        when = this_month if idx % 2 == 0 else last_month
        order = Order.create({
            'partner_id': partner.id,
            'date_order': when,
            # Without a salesperson, Odoo's record rules mean a Sales user sees
            # none of these and their KPI reads 0 — the demo would look broken.
            **({'user_id': salesperson.id} if salesperson else {}),
            'order_line': [(0, 0, {
                'product_id': product.id,
                'product_uom_qty': 1,
                'price_unit': amount,
            })],
        })
        order.action_confirm()
        # action_confirm stamps date_order to now; restore the intended date so
        # the month buckets (and therefore the trend) are meaningful.
        order.write({'date_order': when})
        made += 1
    print('SEED: confirmed sale orders =', made)

    # -- 8. Posted customer invoices across 6 months -------------------------
    # Unpaid on purpose: residual drives `receivables`, and the posted income
    # lines drive the 6-month revenue chart.
    Move = env['account.move']
    for offset in range(6):
        when = (today.replace(day=1) - relativedelta(months=offset)) + relativedelta(days=5)
        partner, amount = customers[offset % len(customers)]
        inv = Move.create({
            'move_type': 'out_invoice',
            'partner_id': partner.id,
            'invoice_date': when,
            'date': when,
            'invoice_line_ids': [(0, 0, {
                'product_id': product.id,
                'quantity': 1,
                'price_unit': amount * (0.6 + 0.1 * offset),
            })],
        })
        inv.action_post()
    print('SEED: posted customer invoices =',
          Move.search_count([('move_type', '=', 'out_invoice'), ('state', '=', 'posted')]))

    # -- 9. Posted vendor bills (payables) -----------------------------------
    for partner, amount in vendors:
        bill = Move.create({
            'move_type': 'in_invoice',
            'partner_id': partner.id,
            'invoice_date': today - relativedelta(days=10),
            'date': today - relativedelta(days=10),
            'invoice_line_ids': [(0, 0, {
                'product_id': product.id,
                'quantity': 1,
                'price_unit': amount,
            })],
        })
        bill.action_post()
    print('SEED: posted vendor bills =',
          Move.search_count([('move_type', '=', 'in_invoice'), ('state', '=', 'posted')]))

    # -- 10. Bank opening balance (cash_bank) ---------------------------------
    bank_journal = env['account.journal'].search([('type', '=', 'bank')], limit=1)
    if bank_journal:
        bank_acct = bank_journal.default_account_id
        equity = env['account.account'].search([('account_type', '=', 'equity')], limit=1)
        if bank_acct and equity:
            entry = Move.create({
                'move_type': 'entry',
                'journal_id': bank_journal.id,
                'date': today - relativedelta(months=6),
                'ref': 'Opening bank balance',
                'line_ids': [
                    (0, 0, {'account_id': bank_acct.id, 'debit': 250000.0, 'credit': 0.0}),
                    (0, 0, {'account_id': equity.id, 'debit': 0.0, 'credit': 250000.0}),
                ],
            })
            entry.action_post()
            print('SEED: bank opening entry posted')

    # -- 11. Open activities -------------------------------------------------
    act_type = env.ref('mail.mail_activity_data_todo', raise_if_not_found=False)
    partner_model = env['ir.model']._get_id('res.partner')
    # Spread across several users: activities are per-assignee, so putting them
    # all on admin leaves every other role showing 0.
    assignees = [admin] + [u for u in (
        env['res.users'].search([('login', 'in', [s[1] for s in STAFF])])
    )]
    for idx, (partner, _amt) in enumerate(customers):
        env['mail.activity'].create({
            'res_model_id': partner_model,
            'res_id': partner.id,
            'user_id': assignees[idx % len(assignees)].id,
            'summary': 'Follow up on %s renewal' % partner.name,
            'date_deadline': today + relativedelta(days=3),
            **({'activity_type_id': act_type.id} if act_type else {}),
        })
    print('SEED: open activities =', env['mail.activity'].search_count([]))

    env.cr.commit()
    print('SEED: done')
