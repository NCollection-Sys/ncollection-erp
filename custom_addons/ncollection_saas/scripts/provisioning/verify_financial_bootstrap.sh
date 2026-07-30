#!/usr/bin/env bash
# shellcheck disable=SC2015
# ============================================================================
#  P3-T01 — interim OCA financial bootstrap proof (local; heavy — not CI)
# ============================================================================
#  Acceptance: "a freshly provisioned Enterprise tenant can run a Trial Balance
#  immediately." The Enterprise plan provisions account + account_financial_report
#  + ncollection_mis_templates. This proves, end to end, that:
#    1. the Enterprise financial set installs CLEANLY on a fresh tenant DB,
#    2. against UAE data (the shipped 'ae' localisation → UAE CoA + AED),
#    3. the OCA Trial Balance report actually RUNS and returns account lines.
#
#  The CI-safe unit test (ncollection_subscription/tests/test_plan.py) proves the
#  plan CARRIES the set (so the engine installs it); THIS exercises the real
#  install + report run. Idempotent: drops + rebuilds its own fixture DB.
#  Fixture DB: fintest  (owned by this script only).
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/../../../.."   # repo root

DB="${FIN_BOOTSTRAP_DB:-fintest}"
DC=(docker compose)
DBARGS=(--db_host=db --db_user=odoo --db_password=odoo)
# The Enterprise plan's provisioning set + the UAE localisation as the data
# context (already shipped by P3-T04/T05; used here only as UAE demo data).
MODULES="account,account_financial_report,ncollection_mis_templates,ncollection_account_localization_uae"
pass=0; fail=0
ok(){ echo "  ✅ PASS: $1"; pass=$((pass + 1)); }
no(){ echo "  ❌ FAIL: $1"; fail=$((fail + 1)); }
hr(){ echo "----------------------------------------------------------------------"; }
drop_db(){ "${DC[@]}" exec -T db psql -U odoo -d postgres -c \
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$1' AND pid<>pg_backend_pid()" >/dev/null 2>&1 || true
  "${DC[@]}" exec -T db dropdb -U odoo --if-exists "$1" >/dev/null 2>&1 || true; }

echo "P3-T01 — OCA financial bootstrap proof (DB: $DB)"
hr
echo "== 1) install the Enterprise financial set on a fresh UAE tenant =="
drop_db "$DB"
"${DC[@]}" exec -T db createdb -U odoo -O odoo "$DB" >/dev/null 2>&1
if "${DC[@]}" exec -T odoo odoo -d "$DB" -i "$MODULES" --without-demo=True --no-http \
     --stop-after-init "${DBARGS[@]}" >/dev/null 2>&1; then
  ok "financial set installed cleanly ($MODULES)"
else
  no "financial set failed to install"; hr; echo "SUMMARY: $pass passed, $fail failed."; exit 1
fi

# account_financial_report + mis_builder (pulled by ncollection_mis_templates)
# must be state=installed.
for m in account_financial_report mis_builder ncollection_mis_templates; do
  st="$("${DC[@]}" exec -T db psql -U odoo -d "$DB" -tAc \
    "SELECT state FROM ir_module_module WHERE name='$m'" 2>/dev/null | tr -d '[:space:]')"
  [ "$st" = "installed" ] && ok "$m installed" || no "$m not installed (state=$st)"
done

hr
echo "== 2) post a UAE journal entry and RUN the Trial Balance report =="
out="$("${DC[@]}" exec -T odoo odoo shell -d "$DB" --no-http --log-level=error "${DBARGS[@]}" 2>&1 <<'PY'
company = env.ref('base.main_company')
env = env(context=dict(env.context, allowed_company_ids=company.ids))
Account = env['account.account']
accs = Account.search([('company_ids', 'in', company.ids)], limit=2)
journal = env['account.journal'].search(
    [('type', '=', 'general'), ('company_id', '=', company.id)], limit=1)
move = env['account.move'].create({
    'journal_id': journal.id, 'date': '2026-06-15',
    'line_ids': [
        (0, 0, {'account_id': accs[0].id, 'name': 'fin bootstrap', 'debit': 100.0, 'credit': 0.0}),
        (0, 0, {'account_id': accs[1].id, 'name': 'fin bootstrap', 'debit': 0.0, 'credit': 100.0}),
    ]})
move.action_post()
wiz = env['trial.balance.report.wizard'].create({
    'date_from': '2026-01-01', 'date_to': '2026-12-31', 'target_move': 'posted',
    'hide_account_at_0': False, 'show_hierarchy': False, 'company_id': company.id,
    'account_ids': False, 'fy_start_date': '2026-01-01', 'show_partner_details': False})
data = wiz._prepare_report_data()
res = env['report.account_financial_report.trial_balance']._get_report_values(wiz, data)
assert res and res.get('trial_balance'), 'Trial Balance returned no data'
print('TRIALBALANCE_OK lines=%d currency=%s' % (len(res['trial_balance']), company.currency_id.name))
PY
)"
echo "$out" | grep -q 'TRIALBALANCE_OK' \
  && ok "Trial Balance ran on the fresh UAE tenant ($(echo "$out" | grep -o 'TRIALBALANCE_OK.*'))" \
  || { no "Trial Balance did not run"; echo "$out" | tail -8; }

hr
echo "SUMMARY: $pass passed, $fail failed."
drop_db "$DB"
[ "$fail" -eq 0 ] && echo "✅ Financial bootstrap verified (install + Trial Balance on UAE data)." || exit 1
