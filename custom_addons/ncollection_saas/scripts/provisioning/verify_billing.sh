#!/usr/bin/env bash
# shellcheck disable=SC2015
# ============================================================================
#  P2-T11 — end-to-end subscription-billing proof (local; heavy — not CI)
# ============================================================================
#  Against a platform DB with ncollection_saas installed, proves the billing
#  engine on the ADMIN DB (no tenant DB is created — billing is admin-only):
#    1. the lazy bootstrap loads the UAE chart (AED) + 5% VAT + product,
#    2. ACTIVATING a subscription posts exactly ONE invoice for the period,
#       priced from the plan with UAE VAT 5% applied, linked to tenant + sub,
#    3. re-running the activation billing is IDEMPOTENT (still one invoice),
#    4. RENEWING posts exactly one more invoice for the new period,
#    5. a mid-cycle UPGRADE posts exactly one prorated invoice.
#
#  The CI-safe unit tests (tests/test_billing.py) cover the same guarantees in
#  a rolled-back transaction; THIS proves them against a real installed DB with
#  a really-loaded chart of accounts. Usage:  PLATFORM_DB=saastest bash <this>
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/../../../.."   # repo root

PLATFORM_DB="${PLATFORM_DB:-saastest}"
DC=(docker compose)
DBARGS=(--db_host=db --db_user=odoo --db_password=odoo)
pass=0; fail=0
ok(){ echo "  ✅ PASS: $1"; pass=$((pass + 1)); }
no(){ echo "  ❌ FAIL: $1"; fail=$((fail + 1)); }
hr(){ echo "----------------------------------------------------------------------"; }

PLAN_A="BILLA$$"   # unique per run — plan.code is SQL-unique
PLAN_B="BILLB$$"

echo "== cleanup any prior run (billing fixtures on $PLATFORM_DB) =="
"${DC[@]}" exec -T odoo odoo shell -d "$PLATFORM_DB" --no-http --log-level=error "${DBARGS[@]}" <<'PY' 2>/dev/null || true
subs = env['ncollection.subscription'].search([('name','=','SUB-BILLVERIFY')])
env['account.move'].search([('ncollection_subscription_id','in',subs.ids)]).button_draft()
env['account.move'].search([('ncollection_subscription_id','in',subs.ids)]).unlink()
tenants = subs.tenant_id
subs.unlink()
tenants.unlink()
env.cr.commit()
PY

echo "== run the full billing lifecycle on $PLATFORM_DB =="
"${DC[@]}" exec -T odoo odoo shell -d "$PLATFORM_DB" --no-http --log-level=warn "${DBARGS[@]}" > /tmp/verify_billing_out.txt 2>/dev/null <<PY
Plan = env['ncollection.subscription.plan']
Tenant = env['ncollection.tenant']
Sub = env['ncollection.subscription']
a = Plan.create({'name':'Bill A','code':'$PLAN_A','monthly_price':100.0,'yearly_price':1000.0,'max_users':5})
b = Plan.create({'name':'Bill B','code':'$PLAN_B','monthly_price':300.0,'yearly_price':3000.0,'max_users':20})
# database_status='ready' => activation's provisioning trigger is a no-op; billing only
t = Tenant.create({'company_name':'Bill Verify Co','email':'ops@billverify.test','plan_id':a.id,'database_status':'ready'})
s = Sub.create({'name':'SUB-BILLVERIFY','tenant_id':t.id,'plan_id':a.id,'billing_cycle':'monthly'})
t.subscription_id = s

s.action_activate()
env.cr.commit()
inv = s.invoice_ids
print('ACT_COUNT=%d' % len(inv))
print('ACT_STATE=%s' % (inv.state if inv else '-'))
print('ACT_CUR=%s' % (inv.currency_id.name if inv else '-'))
print('ACT_UNTAXED=%.2f' % (inv.amount_untaxed if inv else 0))
print('ACT_TAX=%.2f' % (inv.amount_tax if inv else 0))
print('ACT_TOTAL=%.2f' % (inv.amount_total if inv else 0))
print('ACT_LINKED=%s' % (bool(inv and inv.ncollection_tenant_id == t and inv.ncollection_subscription_id == s)))

again = s._create_subscription_invoice(period_key=str(s.end_date))
print('IDEMP_COUNT=%d' % len(s.invoice_ids))
print('IDEMP_SAME=%s' % (again == inv))

s.action_renew()
env.cr.commit()
print('RENEW_COUNT=%d' % len(s.invoice_ids))

n0 = len(s.invoice_ids)
s.plan_id = b.id
env.cr.commit()
pror = s.invoice_ids.filtered(lambda m: (m.ncollection_period_key or '').startswith('upgrade:'))
print('UPGRADE_NEW=%d' % (len(s.invoice_ids) - n0))
print('UPGRADE_PRORATIONS=%d' % len(pror))
print('PAYSTATE=%s' % s.invoice_payment_state)
PY

cat /tmp/verify_billing_out.txt
hr
V(){ grep -E "^$1=" /tmp/verify_billing_out.txt | head -1 | cut -d= -f2-; }

[ "$(V ACT_COUNT)" = "1" ]        && ok "activation posts exactly one invoice"        || no "activation invoice count ($(V ACT_COUNT))"
[ "$(V ACT_STATE)" = "posted" ]   && ok "activation invoice is posted"                || no "activation invoice not posted ($(V ACT_STATE))"
[ "$(V ACT_CUR)" = "AED" ]        && ok "invoice currency is AED"                     || no "currency not AED ($(V ACT_CUR))"
[ "$(V ACT_TAX)" = "5.00" ]       && ok "UAE VAT 5% applied (5.00 on 100.00)"         || no "VAT not 5.00 ($(V ACT_TAX))"
[ "$(V ACT_TOTAL)" = "105.00" ]   && ok "invoice total 105.00 (100 + 5% VAT)"         || no "total not 105.00 ($(V ACT_TOTAL))"
[ "$(V ACT_LINKED)" = "True" ]    && ok "invoice linked to tenant + subscription"     || no "invoice links missing"
[ "$(V IDEMP_COUNT)" = "1" ] && [ "$(V IDEMP_SAME)" = "True" ] && ok "re-billing same period is idempotent" || no "not idempotent (count $(V IDEMP_COUNT), same $(V IDEMP_SAME))"
[ "$(V RENEW_COUNT)" = "2" ]      && ok "renewal posts exactly one more invoice"      || no "renewal count ($(V RENEW_COUNT))"
[ "$(V UPGRADE_NEW)" = "1" ] && [ "$(V UPGRADE_PRORATIONS)" = "1" ] && ok "upgrade posts one proration invoice" || no "proration wrong (new $(V UPGRADE_NEW), prorations $(V UPGRADE_PRORATIONS))"
[ "$(V PAYSTATE)" = "not_paid" ]  && ok "subscription tracks payment status (not_paid)" || no "payment status ($(V PAYSTATE))"

hr
echo "== cleanup =="
"${DC[@]}" exec -T odoo odoo shell -d "$PLATFORM_DB" --no-http --log-level=error "${DBARGS[@]}" <<'PY' 2>/dev/null || true
subs = env['ncollection.subscription'].search([('name','=','SUB-BILLVERIFY')])
moves = env['account.move'].search([('ncollection_subscription_id','in',subs.ids)])
moves.button_draft(); moves.unlink()
tenants = subs.tenant_id
subs.unlink(); tenants.unlink()
env.cr.commit()
PY

hr
echo "RESULTS: $pass passed, $fail failed."
[ "$fail" -eq 0 ] || exit 1
echo "✅ verify_billing: billing engine proven on $PLATFORM_DB."
