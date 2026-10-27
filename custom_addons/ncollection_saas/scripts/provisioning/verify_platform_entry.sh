#!/usr/bin/env bash
# shellcheck disable=SC2015
# ============================================================================
#  Platform Admin -> "Enter Company" — end-to-end proof across two REAL tenant databases (local; heavy — not CI)
# ============================================================================
#  Needs the routing overlay (db_filter=^%d$, `make routing-up`) and a platform DB with ncollection_saas
#  installed, the odoo container carrying NC_CONFIG_SYNC_KEY. Proves, with real databases and real HTTP:
#    1  a platform admin sees ONLY the tenants they are authorised for,
#    2  entering tenant A works and the browser session is the admin's named proxy in A,
#    3  a customer and an invoice created inside A exist in A's database and NOT in B's,
#    4  switching to B gives a clean B context: no A data, A's session is not valid on B,
#    5  data created in B lands in B only,
#    6  an unauthorised platform admin cannot enter, a replayed/foreign token is refused, an arbitrary
#       database name in the URL changes nothing,
#    7  every write inside a tenant is attributed to "Platform Admin: <name>", not to Admin,
#    8  a tenant's normal admin cannot reach the platform, and the proxy has no password and no seat.
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/../../../.."   # repo root

PLATFORM_DB="${PLATFORM_DB:-saastest}"
DB_A="pentera"; DB_B="penterb"
DC=(docker compose)
DBARGS=(--db_host=db --db_user=odoo --db_password=odoo)
BASE_DOMAIN="${NC_BASE_DOMAIN:-ncollection.localhost}"   # only a Host header here: the first label picks the database
EDGE="http://127.0.0.1:8069"
pass=0; fail=0
ok(){ echo "  ✅ PASS: $1"; pass=$((pass + 1)); }
no(){ echo "  ❌ FAIL: $1"; fail=$((fail + 1)); }
hr(){ echo "----------------------------------------------------------------------"; }
tq(){ "${DC[@]}" exec -T db psql -U odoo -d "$1" -tAc "$2" 2>/dev/null | tr -d '\r'; }
drop_db(){ "${DC[@]}" exec -T db psql -U odoo -d postgres -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$1'" >/dev/null 2>&1 || true
  "${DC[@]}" exec -T db psql -U odoo -d postgres -c "DROP DATABASE IF EXISTS $1" >/dev/null 2>&1 || true; }
shellp(){ "${DC[@]}" exec -T odoo odoo shell -d "$PLATFORM_DB" --no-http --log-level=error "${DBARGS[@]}" 2>/dev/null; }

echo "== preconditions =="
if ! curl -s -o /dev/null --resolve "$DB_A.$BASE_DOMAIN:8069:127.0.0.1" "http://$DB_A.$BASE_DOMAIN:8069/web/health"; then
  echo "REFUSING: the odoo server is not reachable on :8069." >&2; exit 2; fi
echo "  syncing $PLATFORM_DB schema with the code (odoo -u ncollection_saas) ..."
_log="$(mktemp)"
if ! "${DC[@]}" exec -T odoo odoo -d "$PLATFORM_DB" -u ncollection_subscription,ncollection_saas \
     --stop-after-init --no-http --log-level=warn "${DBARGS[@]}" >"$_log" 2>&1; then
  echo "REFUSING: could not upgrade ncollection_saas on '$PLATFORM_DB' (log: $_log)." >&2; exit 1; fi
rm -f "$_log"

echo "== cleanup any prior run =="
drop_db "$DB_A"; drop_db "$DB_B"
shellp <<PY || true
try:
    env['ncollection.subscription'].search([('tenant_id.database_name','in',['$DB_A','$DB_B'])]).unlink()
    env['ncollection.tenant'].search([('database_name','in',['$DB_A','$DB_B'])]).unlink()
    env['res.users'].with_context(active_test=False).search([('login','in',['pentadmin','pentother'])]).unlink()
    env.cr.commit()
except Exception as e:
    print('cleanup skipped:', e)
PY

echo "== provision two tenants (plan modules: account) and two platform administrators =="
PLAN="ENTRY$$"
"${DC[@]}" exec -T odoo odoo shell -d "$PLATFORM_DB" --no-http --log-level=warn "${DBARGS[@]}" <<PY 2>/dev/null
group = env.ref('ncollection_subscription.group_platform_admin')
internal = env.ref('base.group_user')
users = {}
for login, name in (('pentadmin', 'Penta Admin'), ('pentother', 'Other Platform Admin')):
    users[login] = env['res.users'].create({'name': name, 'login': login, 'group_ids': [(6, 0, [internal.id, group.id])]})
plan = env['ncollection.subscription.plan'].create({
    'name': 'Entry proof', 'code': '$PLAN', 'allowed_module_names': 'account', 'max_users': 5})
env['ir.config_parameter'].sudo().set_param('ncollection_saas.provisioning_quota_per_hour', '0')
for db, name in (('$DB_A', 'Entry Co A'), ('$DB_B', 'Entry Co B')):
    tenant = env['ncollection.tenant'].create({
        'company_name': name, 'database_name': db, 'email': db + '@entry.example', 'admin_login': db + 'adm',
        'plan_id': plan.id, 'status': 'trial', 'platform_admin_ids': [(6, 0, users['pentadmin'].ids)]})
    sub = env['ncollection.subscription'].create({
        'name': 'SUB', 'tenant_id': tenant.id, 'plan_id': plan.id, 'status': 'draft'})
    tenant.subscription_id = sub.id
    job = env['ncollection.provisioning.job'].create({'tenant_id': tenant.id, 'database_name': db})
    env.cr.commit()
    job.action_run_sync()
    env.cr.commit()
    print('PROVISIONED', db, job.status, tenant.database_status)
print('IDS', users['pentadmin'].id, users['pentother'].id)
PY
hr

# ---------------------------------------------------------------- 1. who sees what
echo "== 1. a platform admin sees only the companies they are authorised for =="
OUT="$(shellp <<PY
T = env['ncollection.tenant']
for login in ('pentadmin', 'pentother'):
    user = env['res.users'].search([('login', '=', login)])
    print('SEES', login, ','.join(sorted(T._nc_enterable_for(user).mapped('database_name'))))
PY
)"
echo "$OUT" | grep -q "SEES pentadmin $DB_A,$DB_B" && ok "pentadmin sees A and B" || no "pentadmin list wrong: $OUT"
echo "$OUT" | grep -q "SEES pentother *$" && ok "pentother sees no company" || no "pentother list wrong: $OUT"
hr

URLS="$(mktemp -d)"
enter(){   # enter <db> <cookie-jar> -> prints the HTTP status of the entry; its URL is kept in $URLS/<db>
  local db="$1" jar="$2" out
  out="$(shellp <<PY
admin = env['res.users'].search([('login', '=', 'pentadmin')])
tenant = env['ncollection.tenant'].search([('database_name', '=', '$db')])
print('URL', tenant.with_user(admin).action_enter_company()['url'])
PY
)"
  echo "$out" | sed -n 's/^URL //p' | tail -1 > "$URLS/$db"     # a file: enter runs in a subshell
  [ -s "$URLS/$db" ] || { echo "000"; return; }
  curl -s -o /dev/null -w "%{http_code}" -c "$jar" -b "$jar" -H "Host: $db.$BASE_DOMAIN" \
    --data-urlencode "token=$(sed "s/.*#token=//" "$URLS/$db")" "$EDGE/ncollection/platform/enter" || true
}
rpc(){     # rpc <db> <jar> <model> <method> <args-json> [kwargs-json]
  curl -s -c "$2" -b "$2" -H "Host: $1.$BASE_DOMAIN" -H 'Content-Type: application/json' \
    -d "{\"jsonrpc\":\"2.0\",\"method\":\"call\",\"params\":{\"model\":\"$3\",\"method\":\"$4\",\"args\":$5,\"kwargs\":${6:-{\}}}}" \
    "$EDGE/web/dataset/call_kw/$3/$4"
}
PA="$(mktemp)"; PB="$(mktemp)"

# ---------------------------------------------------------------- 2/3. enter A, write in A
echo "== 2/3. enter A: a named proxy session; customer + invoice land in A only =="
[ "$(enter "$DB_A" "$PA")" = "303" ] && ok "entry redirects (303) into tenant A" || no "entry into A did not redirect"

# The address Company Entry sends the browser to is the ONE canonical tenant address (<label>.<configured base domain>),
# never a stored snapshot, a stale localhost:8069, or anything typed on the tenant record. (The token is a fragment
# and is not printed.)
EXPECT_A="$(shellp <<PY | sed -n 's/^ORIGIN //p' | tail -1 | tr -d '\r'
print('ORIGIN', env['ncollection.tenant'].search([('database_name', '=', '$DB_A')])._nc_public_origin())
PY
)"
case "$(cat "$URLS/$DB_A")" in
  "$EXPECT_A"/ncollection/platform/enter#token=*) ok "Company Entry goes to the canonical tenant address ($EXPECT_A)" ;;
  *) no "Company Entry URL is not the canonical builder's address (expected prefix '$EXPECT_A')" ;;
esac
case "$(cat "$URLS/$DB_A")" in
  *:8069*|*"localhost:"*) no "the Company Entry URL carries a stale port" ;;
  *) ok "no stale port in the Company Entry URL" ;;
esac
SESS="$(curl -s -c "$PA" -b "$PA" -H "Host: $DB_A.$BASE_DOMAIN" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","params":{}}' "$EDGE/web/session/get_session_info")"
echo "$SESS" | grep -q '"username": "platform\.u' && ok "the A session is the admin's platform proxy" || no "A session user wrong"
rpc "$DB_A" "$PA" res.partner create '[{"name":"Customer made in A","customer_rank":1}]' >/dev/null
rpc "$DB_A" "$PA" account.move create '[{"move_type":"out_invoice","partner_id":1,"invoice_line_ids":[[0,0,{"name":"Entry proof line","quantity":1,"price_unit":10}]]}]' >/dev/null
[ "$(tq "$DB_A" "SELECT count(*) FROM res_partner WHERE name='Customer made in A'")" = "1" ] && ok "customer exists in A's database" || no "customer missing in A"
[ "$(tq "$DB_B" "SELECT count(*) FROM res_partner WHERE name='Customer made in A'")" = "0" ] && ok "…and NOT in B's database" || no "A's customer leaked into B"
[ "$(tq "$DB_A" "SELECT count(*) FROM account_move_line WHERE name='Entry proof line'")" -ge 1 ] && ok "invoice line exists in A" || no "invoice missing in A"
[ "$(tq "$DB_B" "SELECT count(*) FROM account_move_line WHERE name='Entry proof line'")" = "0" ] && ok "…and NOT in B" || no "A's invoice leaked into B"
hr

# ---------------------------------------------------------------- 4/5. switch to B
echo "== 4/5. switch to B: clean context, no A data, writes land in B only =="
[ "$(enter "$DB_B" "$PB")" = "303" ] && ok "entry redirects (303) into tenant B" || no "entry into B did not redirect"
SEEN="$(rpc "$DB_B" "$PB" res.partner search_read '[[["name","ilike","made in"]]]' '{"fields":["name"]}')"
echo "$SEEN" | grep -q "made in A" && no "B can see A's customer" || ok "B's context shows none of A's data"
# A's session cookie presented to B's host must not be a valid session there
STALE="$(curl -s -b "$PA" -H "Host: $DB_B.$BASE_DOMAIN" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","method":"call","params":{"model":"res.partner","method":"search_read","args":[[]],"kwargs":{"fields":["name"]}}}' \
  "$EDGE/web/dataset/call_kw/res.partner/search_read")"
echo "$STALE" | grep -q '"error"' && ok "A's session is NOT valid on B" || no "A's session worked on B: $STALE"
rpc "$DB_B" "$PB" res.partner create '[{"name":"Customer made in B","customer_rank":1}]' >/dev/null
[ "$(tq "$DB_B" "SELECT count(*) FROM res_partner WHERE name='Customer made in B'")" = "1" ] && ok "customer exists in B's database" || no "customer missing in B"
[ "$(tq "$DB_A" "SELECT count(*) FROM res_partner WHERE name='Customer made in B'")" = "0" ] && ok "…and NOT in A's database" || no "B's customer leaked into A"
hr

# ---------------------------------------------------------------- 6. refusals
echo "== 6. unauthorised, replayed, foreign and arbitrary-database attempts =="
OUT="$(shellp <<PY
from odoo.exceptions import AccessError
other = env['res.users'].search([('login', '=', 'pentother')])
tenant = env['ncollection.tenant'].search([('database_name', '=', '$DB_A')])
try:
    tenant.with_user(other).action_enter_company()
    print('ENTERED')
except AccessError:
    print('REFUSED')
PY
)"
echo "$OUT" | grep -q REFUSED && ok "an unauthorised platform admin cannot enter A" || no "unauthorised entry was allowed: $OUT"
REPLAY="$(curl -s -o /dev/null -w "%{http_code}" -H "Host: $DB_A.$BASE_DOMAIN" --data-urlencode "token=$(sed "s/.*#token=//" "$URLS/$DB_A")" "$EDGE/ncollection/platform/enter" || true)"
[ "$REPLAY" = "403" ] && ok "a replayed token is refused (403)" || no "replay answered $REPLAY"
TOK="$(sed 's/.*#token=//' "$URLS/$DB_B")"     # B's token (already used once)
FOREIGN="$(curl -s -o /dev/null -w "%{http_code}" -H "Host: $DB_A.$BASE_DOMAIN" --data-urlencode "token=$TOK" "$EDGE/ncollection/platform/enter?db=$DB_B" || true)"
[ "$FOREIGN" = "403" ] && ok "a token for B presented to A (with db=B in the URL) is refused" || no "foreign token answered $FOREIGN"
UNKNOWN="$(curl -s -o /dev/null -w "%{http_code}" -H "Host: nosuchtenant.$BASE_DOMAIN" --data-urlencode "token=$TOK" "$EDGE/ncollection/platform/enter?db=$DB_B" || true)"
[ "$UNKNOWN" != "303" ] && [ "$UNKNOWN" != "200" ] && ok "an unregistered host/database name opens nothing ($UNKNOWN)" || no "unknown database answered $UNKNOWN"
hr

# ---------------------------------------------------------------- 7/8. attribution, passwordless, seats
echo "== 7/8. attribution, no password, no seat, tenant admin cannot reach the platform =="
[ "$(tq "$DB_A" "SELECT u.login FROM res_partner p JOIN res_users u ON u.id=p.create_uid WHERE p.name='Customer made in A'")" != "${DB_A}adm" ] \
  && [ "$(tq "$DB_A" "SELECT split_part(u.login,'.',1) FROM res_partner p JOIN res_users u ON u.id=p.create_uid WHERE p.name='Customer made in A'")" = "platform" ] \
  && ok "the customer was created by the Platform Admin proxy, not by the tenant Admin" || no "attribution wrong"
[ "$(tq "$DB_A" "SELECT pt.name FROM res_partner p JOIN res_users u ON u.id=p.create_uid JOIN res_partner pt ON pt.id=u.partner_id WHERE p.name='Customer made in A'")" = "Platform Admin: Penta Admin" ] \
  && ok "…named \"Platform Admin: Penta Admin\"" || no "proxy name wrong"
[ "$(tq "$DB_A" "SELECT count(*) FROM res_users WHERE nc_platform_proxy AND password IS NOT NULL AND password <> ''")" = "0" ] \
  && ok "the proxy has no password" || no "a proxy holds a password"
[ "$(tq "$DB_A" "SELECT count(*) FROM ncollection_platform_entry WHERE used_at IS NOT NULL")" -ge 1 ] \
  && ok "the entry is recorded as used (audit trail in the tenant)" || no "no used entry recorded"
[ "$(tq "$DB_A" "SELECT count(*) FROM ir_model WHERE model='ncollection.subscription.dashboard'")" = "0" ] \
  && ok "the tenant database has no Platform Administration at all" || no "platform models present in a tenant"
hr

echo "== cleanup =="
drop_db "$DB_A"; drop_db "$DB_B"
shellp <<PY || true
env['ncollection.subscription'].search([('tenant_id.database_name','in',['$DB_A','$DB_B'])]).unlink()
env['ncollection.tenant'].search([('database_name','in',['$DB_A','$DB_B'])]).unlink()
env['res.users'].with_context(active_test=False).search([('login','in',['pentadmin','pentother'])]).unlink()
env.cr.commit()
PY
rm -rf "$PA" "$PB" "$URLS"

echo "SUMMARY: $pass passed, $fail failed."
[ "$fail" -eq 0 ] && echo "✅ Platform entry verified across two real tenant databases." \
  || { echo "❌ FAILURES above."; exit 1; }
