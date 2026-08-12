#!/usr/bin/env bash
# ============================================================================
#  P1-T20 — create the E2E test tenants on the running routing stack
# ============================================================================
#  Two tenants on DIFFERENT plans (so visibility + license journeys diverge)
#  plus the admin platform DB. Idempotent. Requires the routing overlay up
#  (db_filter ON): `make routing-up`. Reuses the P1-T06 setup pattern.
#
#  Both install the SAME modules (crm + sale); they differ only in the licensed
#  set (allowed_module_names) — so e2eclientb has `sale` INSTALLED but UNLICENSED,
#  which is exactly what P1-T09 (menu hidden) and P1-T10 (access blocked) enforce.
#    e2eclienta = Pro   plan: allowed = "crm,sale"  (Sales visible + usable)
#    e2eclientb = Basic plan: allowed = "crm"       (Sales installed, hidden+blocked)
#    e2eadmin   = platform DB (routing target)
#
#  FIXTURE NAMESPACE — these names are deliberately prefixed `e2e*`. The P1-T06
#  routing proof owns rtclienta/rtclientb/rtadmin and `make routing-clean` drops those.
#  Sharing one namespace across two suites meant either could destroy the other's
#  fixtures; the prefix makes that structurally impossible. The names must stay
#  ALPHANUMERIC: db_filter=^%d$ maps a subdomain to the DB of the same name, and
#  underscores are invalid in hostnames while hyphens need Postgres quoting.
#
#  Admin login for every tenant: admin / $E2E_ADMIN_PW (default "admin").
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/../.."   # repo root
# Pinned once, right after the cd that establishes it. The asserter is invoked
# from six places below; resolving it through $PWD each time would silently
# break if anything in between ever changes directory.
REPO_ROOT="$PWD"

# This suite requires the ROUTING stack: base (db+odoo) + dev (the nginx edge)
# + routing (db_filter=^%d$). `docker compose` alone loads ONLY the base file,
# which does not define `nginx` — so service lookups like `ps -q nginx` fail.
# Honour a caller-supplied COMPOSE_FILE (CI sets it); otherwise default to the
# exact trio `make routing-up` starts.
if [ -n "${COMPOSE_FILE:-}" ]; then
  DC=(docker compose)
else
  DC=(docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.routing.yml)
fi
DBARGS=(--db_host=db --db_user=odoo --db_password=odoo)
ADMIN_PW="${E2E_ADMIN_PW:-admin}"

db_exists(){ "${DC[@]}" exec -T db psql -U odoo -d postgres -tAc \
  "SELECT 1 FROM pg_database WHERE datname='$1'" 2>/dev/null | grep -q 1; }
# "provisioned" = the tenant modules are actually installed (guards against a
# stale base-only DB left by another test, e.g. P1-T06 routing).
tenant_provisioned(){ "${DC[@]}" exec -T db psql -U odoo -d "$1" -tAc \
  "SELECT 1 FROM ir_module_module WHERE name='ncollection_core' AND state='installed'" \
  2>/dev/null | grep -q 1; }
drop_db(){ "${DC[@]}" exec -T db psql -U odoo -d postgres -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$1'" >/dev/null 2>&1 || true
  "${DC[@]}" exec -T db psql -U odoo -d postgres -c "DROP DATABASE IF EXISTS $1" >/dev/null 2>&1 || true; }

# create_tenant <db> <modules> <allowed_module_names>
create_tenant(){
  local db="$1" modules="$2" allowed="$3"
  if tenant_provisioned "$db"; then
    # A reused fixture still has YESTERDAY's schema. Skipping the upgrade is
    # how a model change turns into "column ... does not exist" from deep
    # inside a seed script — which is exactly how #264 broke this suite twice
    # (four new columns, then a fifth on the review commit). Upgrade only the
    # ncollection_* modules: they are what drifts, and re-upgrading crm/sale
    # every run would cost minutes for nothing.
    local nc_modules
    nc_modules="$(printf '%s' "$modules" | tr ',' '\n' | grep '^ncollection_' \
      | tr '\n' ',' | sed 's/,$//')"

    # Any module in the LIST that this reused fixture does not actually have yet
    # must be INSTALLED, not upgraded — `-u` on an uninstalled module is a no-op.
    # Without this, adding a module to the list above only takes effect on a
    # fresh database. CI creates tenants from scratch every run, so it would go
    # green while every local run stayed silently broken — the worst kind of
    # asymmetry, because the environment that disagrees is the one nobody
    # watches. Found exactly that way when #363's dashboards rendered in theory
    # and were `uninstalled` in fact.
    # ONE query, not one per module. Asking inside a `while read` loop is the
    # trap scripts/dev/orphan_dbs.sh already documents: `docker compose exec -T`
    # reads stdin, so the first iteration swallows the rest of the loop's input
    # and only the first module is ever examined. That is how this check first
    # reported "nothing missing" against a database where the module was plainly
    # `uninstalled`.
    local installed missing
    installed="$("${DC[@]}" exec -T db psql -U odoo -d "$db" -tAc \
      "SELECT name FROM ir_module_module WHERE state='installed'" 2>/dev/null \
      | tr -d '\r' | tr '\n' ' ')"
    # `printf '%s\n'`, NOT `printf '%s'`. Without the trailing newline `read`
    # returns false on the final element and the loop body never runs for it —
    # so the LAST module in the list is silently never checked. Every earlier
    # module here happened to be installed already, which made `missing` look
    # empty while `ncollection_account_dashboard` sat plainly `uninstalled`.
    missing="$(printf '%s\n' "$modules" | tr ',' '\n' \
      | while IFS= read -r m; do
          [ -n "$m" ] || continue
          case " $installed " in (*" $m "*) : ;; (*) printf '%s\n' "$m" ;; esac
        done | tr '\n' ',' | sed 's/,$//')"

    if [ -n "$missing" ]; then
      echo "  • $db already provisioned — INSTALLING newly-listed: ${missing}"
      if ! "${DC[@]}" exec -T odoo odoo -d "$db" -i "$missing" --without-demo=True \
           --no-http --stop-after-init "${DBARGS[@]}" >"/tmp/e2e_install_$db.log" 2>&1; then
        echo "ERROR: installing $missing on $db failed." >&2
        tail -20 "/tmp/e2e_install_$db.log" >&2
        exit 1
      fi
      # Exit 0 is not success: odoo returns 0 having skipped a module whose
      # dependency is missing, so the check above cannot see that (#385).
      "$REPO_ROOT/scripts/dev/assert_odoo_setup.sh" "/tmp/e2e_install_$db.log" \
        "modules on $db" "if ./oca is empty, run 'make oca'"
    fi

    echo "  • $db already provisioned — upgrading ${nc_modules}"
    if ! "${DC[@]}" exec -T odoo odoo -d "$db" -u "$nc_modules" --without-demo=True \
         --no-http --stop-after-init "${DBARGS[@]}" >"/tmp/e2e_upgrade_$db.log" 2>&1; then
      echo "ERROR: upgrading $nc_modules on $db failed — fixture is stale and" >&2
      echo "       cannot be used. Re-run with 'make e2e-clean' to rebuild." >&2
      tail -20 "/tmp/e2e_upgrade_$db.log" >&2
      exit 1
    fi
      # Exit 0 is not success: odoo returns 0 having skipped a module whose
      # dependency is missing, so the check above cannot see that (#385).
      "$REPO_ROOT/scripts/dev/assert_odoo_setup.sh" "/tmp/e2e_upgrade_$db.log" \
        "modules on $db" "if ./oca is empty, run 'make oca'"
  else
    echo "  • (re)creating $db (modules: $modules) — this takes a minute…"
    drop_db "$db"
    # THIS is the branch CI takes. A fresh ubuntu-latest runner has no
    # databases, so `tenant_provisioned` is false there and the reuse/upgrade
    # branch above never executes. It previously had NO exit check and threw the
    # output away, so a failed install was invisible on the one runner this
    # suite exists to gate. Found by review of #385 — the first version of that
    # ticket wired only the reuse branch and its commit message claimed the file
    # was fully covered. It was not.
    if ! "${DC[@]}" exec -T odoo odoo -d "$db" -i "$modules" --without-demo=True --no-http \
         --stop-after-init "${DBARGS[@]}" >"/tmp/e2e_create_$db.log" 2>&1; then
      echo "ERROR: creating $db with modules '$modules' failed." >&2
      tail -20 "/tmp/e2e_create_$db.log" >&2
      exit 1
    fi
    "$REPO_ROOT/scripts/dev/assert_odoo_setup.sh" "/tmp/e2e_create_$db.log" \
      "$modules on $db" "if ./oca is empty, run 'make oca'"
  fi
  # Deterministic seed: known admin creds + the plan's allowed-module projection.
  "${DC[@]}" exec -T odoo odoo shell -d "$db" --no-http --log-level=error "${DBARGS[@]}" \
    >/dev/null 2>&1 <<PY
admin = env.ref('base.user_admin')
admin.write({'login': 'admin', 'password': '${ADMIN_PW}'})
Cfg = env['ncollection.workspace.config']
cfg = Cfg.search([], limit=1)
vals = {'allowed_module_names': '''${allowed}'''}
cfg.write(vals) if cfg else Cfg.create(vals)
env.cr.commit()
PY
  echo "  • $db seeded (admin/${ADMIN_PW}; allowed='${allowed}')"
}

echo "Setting up E2E tenants…"
# e2eclienta additionally carries the accounting stack so the financial
# dashboards (#363) have something to render. e2eclientb deliberately does NOT:
# its job in this suite is to be the unlicensed half of the plan-gating pair,
# and adding accounting there would blur that contrast for no gain.
# `stock` is here for P6-T02 (#66): portal isolation of DELIVERIES. The
# stock.picking portal rule does not live in `stock` — that module ships no
# group_portal rules at all — it comes from `sale_stock`, which auto-installs
# once `sale` and `stock_account` are both present. So adding `stock` beside the
# existing `sale` is what actually brings the rule under test into existence.
# Licensed too, so the menu is reachable rather than merely installed.
create_tenant e2eclienta \
  "base,ncollection_core,ncollection_branding,crm,sale,account,stock,ncollection_account_dashboard" \
  "crm,sale,account,stock,ncollection_account_dashboard"
create_tenant e2eclientb "base,ncollection_core,ncollection_branding,crm,sale" "crm"

# platform DB — the SaaS platform stack (checkout routes + plans) so the
# e2eadmin.localhost public-checkout journey (P2-T18/T16) has real endpoints.
if ! db_exists e2eadmin; then
  echo "  • creating e2eadmin (base)…"
  if ! "${DC[@]}" exec -T odoo odoo -d e2eadmin -i base --without-demo=True --no-http \
       --stop-after-init "${DBARGS[@]}" >/tmp/e2e_create_e2eadmin.log 2>&1; then
    echo "ERROR: creating e2eadmin failed — the checkout journeys need it." >&2
    tail -20 /tmp/e2e_create_e2eadmin.log >&2
    exit 1
  fi
  "$REPO_ROOT/scripts/dev/assert_odoo_setup.sh" /tmp/e2e_create_e2eadmin.log \
    "base on e2eadmin" "if ./oca is empty, run 'make oca'"
fi
if ! "${DC[@]}" exec -T db psql -U odoo -d e2eadmin -tAc \
     "SELECT 1 FROM ir_module_module WHERE name='ncollection_saas' AND state='installed'" \
     2>/dev/null | grep -q 1; then
  echo "  • installing platform stack (ncollection_saas) on e2eadmin…"
  if ! "${DC[@]}" exec -T odoo odoo -d e2eadmin -i ncollection_saas --without-demo=True --no-http \
       --stop-after-init "${DBARGS[@]}" >/tmp/e2e_install_e2eadmin.log 2>&1; then
    echo "ERROR: installing ncollection_saas on e2eadmin failed." >&2
    tail -20 /tmp/e2e_install_e2eadmin.log >&2
    exit 1
  fi
  "$REPO_ROOT/scripts/dev/assert_odoo_setup.sh" /tmp/e2e_install_e2eadmin.log \
    "ncollection_saas on e2eadmin" "if ./oca is empty, run 'make oca' — queue_job lives there"
else
  # Same staleness trap as create_tenant above. e2eadmin is the PLATFORM db, so
  # it holds ncollection.tenant — every field added to that model lands here.
  echo "  • upgrading platform stack (ncollection_saas) on e2eadmin…"
  if ! "${DC[@]}" exec -T odoo odoo -d e2eadmin -u ncollection_saas --without-demo=True \
       --no-http --stop-after-init "${DBARGS[@]}" >/tmp/e2e_upgrade_e2eadmin.log 2>&1; then
    echo "ERROR: upgrading ncollection_saas on e2eadmin failed — fixture is stale." >&2
    echo "       Re-run with 'make e2e-clean' to rebuild." >&2
    tail -20 /tmp/e2e_upgrade_e2eadmin.log >&2
    exit 1
  fi
  "$REPO_ROOT/scripts/dev/assert_odoo_setup.sh" /tmp/e2e_upgrade_e2eadmin.log \
    "ncollection_saas on e2eadmin" "if ./oca is empty, run 'make oca'"
fi
# Deterministic admin creds + a checkout plan (write-or-create) for the register
# journey. `odoo shell` is a REPL: an exception in the piped script does NOT set a
# non-zero exit, so `set -e` can't see a failed Plan.create()/missing ref. Print a
# sentinel on success and grep for it — a silent seed failure would otherwise
# surface downstream as a confusing invalid_plan/quota_exceeded test error instead
# of a clear setup failure (fail loud, Rule 10). Write-or-create keeps an already
# seeded e2eadmin's plan values current, matching create_tenant's config seed above.
seed_out="$("${DC[@]}" exec -T odoo odoo shell -d e2eadmin --no-http --log-level=error "${DBARGS[@]}" 2>&1 <<PY
env.ref('base.user_admin').write({'login': 'admin', 'password': '${ADMIN_PW}'})
Plan = env['ncollection.subscription.plan']
vals = {'name': 'E2E Starter', 'code': 'E2ESTARTER', 'allowed_module_names': 'crm', 'max_users': 3}
plan = Plan.search([('code', '=', 'E2ESTARTER')], limit=1)
plan.write(vals) if plan else Plan.create(vals)
# Purge stale checkout trial tenants so repeated local runs stay under the per-IP
# trial quota (3/24h) — a leaked trial from an interrupted run would otherwise make
# the register journey fail with quota_exceeded (#214-E). CI is unaffected (fresh db).
Tenant = env['ncollection.tenant'].sudo()
# Register the e2e client tenants in the platform registry so the public checkout
# availability endpoint — registry-based since #226 (it no longer opens a psycopg2
# probe per call) — reports their subdomains as taken. In production the platform
# always holds a tenant record for each provisioned DB; this fixture previously
# created the physical DBs only and relied on that per-call probe.
_e2e_plan = Plan.search([('code', '=', 'E2ESTARTER')], limit=1)
for _db in ('e2eclienta', 'e2eclientb'):
    if not Tenant.search([('database_name', '=', _db)], limit=1):
        Tenant.create({'company_name': _db, 'database_name': _db,
                       'plan_id': _e2e_plan.id, 'database_status': 'ready'})
trials = Tenant.search([('status', '=', 'trial'), ('checkout_source_ip', '!=', False)])
# subscriptions first: subscription.tenant_id is ondelete=restrict, so a tenant
# cannot be unlinked while a subscription still points at it.
env['ncollection.subscription'].sudo().search([('tenant_id', 'in', trials.ids)]).unlink()
trials.unlink()
env.cr.commit()
print('E2EADMIN_SEED_OK')
PY
)"
echo "$seed_out" | grep -q 'E2EADMIN_SEED_OK' || {
  echo "ERROR: e2eadmin seed (admin creds + E2ESTARTER plan) failed — checkout journeys depend on it:" >&2
  echo "$seed_out" >&2
  exit 1; }

# A non-system "business" user with the standard Sales groups on BOTH tenants
# (login: biz / demo1234). Enforcement is bypassed for system users (the
# Owner/admin), so the journeys probe as `biz`: it HAS the Sales groups, so
# Sales is gated purely by the plan license — visible on e2eclienta (licensed),
# hidden + access-denied on e2eclientb (unlicensed). Owner-only menus (Settings)
# stay hidden from `biz` and visible to admin (the role/owner spot check).
echo "  • seeding business user 'biz' (Sales groups, non-system) on both tenants…"
for t in e2eclienta e2eclientb; do
  "${DC[@]}" exec -T odoo odoo shell -d "$t" --no-http --log-level=error "${DBARGS[@]}" \
    >/dev/null 2>&1 <<'PY'
Users = env['res.users']
groups = [env.ref('base.group_user').id]
sale_grp = env.ref('sales_team.group_sale_salesman', raise_if_not_found=False)
if sale_grp:
    groups.append(sale_grp.id)
u = Users.search([('login', '=', 'biz')], limit=1)
if u:
    u.write({'password': 'demo1234', 'group_ids': [(6, 0, groups)]})
else:
    Users.create({'name': 'biz', 'login': 'biz', 'password': 'demo1234',
                  'group_ids': [(6, 0, groups)]})
env.cr.commit()
PY
done

# ---------------------------------------------------------------------------
# Financial-dashboard fixture (#363) — e2eclienta only
# ---------------------------------------------------------------------------
# The 8 ncollection_account_dashboard OWL files had no browser coverage. They
# are the one surface where a stale asset bundle or a mount-time throw is
# invisible to every other gate — exactly the failure dashboard.spec.ts was
# written for after the core dashboard silently vanished from the actions
# registry.
#
# Tours were the obvious alternative and are the wrong tool HERE: `odoo:19`
# ships no browser, so HttpCase tours SKIP and odoo still reports
# "0 failed, 0 error(s)" — green having run nothing. verify.yml already
# installs chromium for Playwright, so this rides on infrastructure that is
# already paid for rather than adding ~300MB to the test image.
#
# Seeded on e2eclienta ONLY: e2eclientb's value as a fixture is being the
# UNLICENSED half of the plan-gating pair, and installing accounting there
# would blur that contrast.
echo "  • seeding financial-dashboard fixture on e2eclienta…"
"${DC[@]}" exec -T odoo odoo shell -d e2eclienta --no-http --log-level=error "${DBARGS[@]}" \
  >/dev/null 2>&1 <<'PY'
# A CEO-role user: the financial dashboards are gated at the RPC (#333/#356/#358),
# so the probe must hold a role the guard admits. The accounting group is granted
# explicitly because ncollection_core/hooks.py links roles to their native
# accounting rights in a post-init hook that a fixture database has not
# necessarily run — without it the dashboard raises an AccessError from deep
# inside the report services, which is not the failure under test.
Users = env['res.users']
groups = [env.ref('base.group_user').id]
for xmlid in ('ncollection_core.group_role_ceo', 'account.group_account_readonly'):
    g = env.ref(xmlid, raise_if_not_found=False)
    if g:
        groups.append(g.id)
u = Users.search([('login', '=', 'fin')], limit=1)
if u:
    u.write({'password': 'demo1234', 'group_ids': [(6, 0, groups)]})
else:
    Users.create({'name': 'fin', 'login': 'fin', 'password': 'demo1234',
                  'group_ids': [(6, 0, groups)]})
env.cr.commit()
PY

# ---------------------------------------------------------------------------
# Department-dashboard fixtures (#363 follow-up) — e2eclienta only.
#
# One user per department role. NOT one user holding all three: the spec asserts
# that a Sales-role user is REFUSED the HR dashboard, and a combined user could
# not tell a correct per-role guard from a single copy-pasted check that admits
# any department role.
#
# The native group mirrors what hooks.py's ROLE_IMPLICATIONS does in a real
# tenant, so the fixture user resembles a production one.
#
# It is NOT load-bearing for these tests, and the first version of this comment
# claimed it was — that it prevented an AccessError from the panel queries. It
# does not: ncollection.aggregation.engine.aggregate() catches AccessError and
# degrades to None/[] rather than propagating, so the dashboard would have
# rendered an empty payload either way. Corrected rather than deleted, because
# the wrong version would have misdirected the next person debugging this.
echo "  • seeding department-role users (dept_sales / dept_hr / dept_wh) on e2eclienta…"
"${DC[@]}" exec -T odoo odoo shell -d e2eclienta --no-http --log-level=error "${DBARGS[@]}" \
  >/dev/null 2>&1 <<'PY'
Users = env['res.users']
FIXTURES = (
    ('dept_sales', 'ncollection_core.group_role_sales',
     'sales_team.group_sale_salesman'),
    ('dept_hr', 'ncollection_core.group_role_hr', 'hr.group_hr_user'),
    ('dept_wh', 'ncollection_core.group_role_warehouse', 'stock.group_stock_user'),
)
for login, role_xmlid, native_xmlid in FIXTURES:
    groups = [env.ref('base.group_user').id]
    for xmlid in (role_xmlid, native_xmlid):
        g = env.ref(xmlid, raise_if_not_found=False)
        if g:
            groups.append(g.id)
    u = Users.search([('login', '=', login)], limit=1)
    if u:
        u.write({'password': 'demo1234', 'group_ids': [(6, 0, groups)]})
    else:
        Users.create({'name': login, 'login': login, 'password': 'demo1234',
                      'group_ids': [(6, 0, groups)]})
env.cr.commit()
PY

# ---------------------------------------------------------------------------
# Portal isolation fixture (P6-T02 / #66) — e2eclienta only
# ---------------------------------------------------------------------------
# TWO partners, each with its own portal user AND its own records. Both halves
# are load-bearing and the pair is the point: a suite asserting "portal user A
# cannot see B's invoice" proves nothing unless B HAS an invoice and A can see
# its own. Before this fixture existed the tenant had ZERO invoices and no real
# portal user, so every such assertion would have passed vacuously — the shape
# this repo has shipped four times (#330, #348, #363, #381).
#
# The invoice is POSTED deliberately. Odoo's core portal rule is
#   state not in ('cancel','draft') AND move_type in (out_invoice, ...)
#   AND partner_id child_of user.commercial_partner_id
# so a DRAFT invoice is invisible even to its rightful owner — the own-records
# control would fail for a reason that has nothing to do with isolation.
#
# The delivery comes from CONFIRMING a sale order rather than creating a picking
# by hand. stock.picking's portal rule (shipped by sale_stock, not stock) is
#   partner_id = user.partner_id  OR  sale_id.partner_id = user.partner_id
# so only a picking with sale_id set exercises the second branch, which is the
# one a real delivery actually takes.
echo "  • seeding portal isolation fixture (portala / portalb) on e2eclienta…"
"${DC[@]}" exec -T odoo odoo shell -d e2eclienta --no-http --log-level=error "${DBARGS[@]}" \
  >/dev/null 2>&1 <<'PY'
Users = env['res.users']
Partner = env['res.partner']
Product = env['product.product']
Move = env['account.move']
SO = env['sale.order']
portal_group = env.ref('base.group_portal')

prod = Product.search([('name', '=', 'P6T02 Widget')], limit=1)
if not prod:
    prod = Product.create({'name': 'P6T02 Widget', 'type': 'consu',
                           'is_storable': True, 'list_price': 100.0})

for tag, company in (('a', 'Portal Alpha Ltd'), ('b', 'Portal Beta Ltd')):
    login = 'portal%s' % tag
    partner = Partner.search([('name', '=', company)], limit=1)
    if not partner:
        partner = Partner.create({'name': company,
                                  'email': '%s@example.com' % login})
    user = Users.search([('login', '=', login)], limit=1)
    if user:
        user.write({'password': 'demo1234', 'partner_id': partner.id,
                    'group_ids': [(6, 0, [portal_group.id])]})
    else:
        # `name` must equal the company: creating a res.users with a name AND
        # an existing partner_id WRITES that name onto the partner. Passing
        # "<company> Contact" here silently renamed the partner, which made a
        # later exact-name lookup miss and a mutation probe a no-op.
        Users.create({'name': company, 'login': login,
                      'password': 'demo1234', 'partner_id': partner.id,
                      'group_ids': [(6, 0, [portal_group.id])]})
    inv = Move.search([('partner_id', '=', partner.id),
                       ('move_type', '=', 'out_invoice')], limit=1)
    if not inv:
        inv = Move.create({
            'move_type': 'out_invoice', 'partner_id': partner.id,
            'invoice_line_ids': [(0, 0, {'product_id': prod.id,
                                         'quantity': 1, 'price_unit': 100.0})]})
    if inv.state == 'draft':
        inv.action_post()
    so = SO.search([('partner_id', '=', partner.id)], limit=1)
    if not so:
        so = SO.create({'partner_id': partner.id,
                        'order_line': [(0, 0, {'product_id': prod.id,
                                               'product_uom_qty': 1})]})
    if so.state in ('draft', 'sent'):
        so.action_confirm()
    # An attachment per party, so the attachment-serving route can be probed
    # with a REAL other-partner id (#403). Portal users have no ORM access to
    # ir.attachment at all — no group_portal ACL row exists — so the only path
    # to one is the controller, and only an HTTP test reaches it.
    Att = env['ir.attachment']
    if not Att.search([('name', '=', 'p6t02-%s.txt' % login)], limit=1):
        Att.create({'name': 'p6t02-%s.txt' % login, 'raw': b'confidential',
                    'res_model': 'account.move', 'res_id': inv.id})
env.cr.commit()
PY

# Refresh the live server's caches so it reflects the new tenants/config/users.
# License enforcement + menu visibility are @ormcache'd per process; the config
# and users were written from separate `odoo shell` processes, so restart the
# server (and reload nginx onto odoo's fresh IP) to guarantee a clean state.
#
# These restarts are LOAD-BEARING: if one silently fails, the suite runs against a
# stale @ormcache and reports confusing results. So they FAIL LOUD — never `|| true`
# on a step whose success we depend on. Container IDs are derived from compose
# rather than hardcoded, so a non-default COMPOSE_PROJECT_NAME still works.
echo "  • refreshing caches (restart odoo + nginx)…"

cid(){ "${DC[@]}" ps -q "$1"; }

odoo_cid="$(cid odoo)"
nginx_cid="$(cid nginx)"
[ -n "$odoo_cid" ] || {
  echo "ERROR: no running 'odoo' service container. Start the stack first: make routing-up" >&2; exit 1; }
[ -n "$nginx_cid" ] || {
  echo "ERROR: no running 'nginx' service container. Start the stack first: make routing-up" >&2; exit 1; }

docker restart "$odoo_cid" >/dev/null || {
  echo "ERROR: failed to restart odoo ($odoo_cid) — enforcement caches would be stale." >&2; exit 1; }

odoo_ready=0
for _ in $(seq 1 30); do
  if docker exec "$odoo_cid" curl -sf http://localhost:8069/web/health >/dev/null 2>&1; then
    odoo_ready=1; break
  fi
  sleep 2
done
[ "$odoo_ready" = 1 ] || {
  echo "ERROR: odoo did not become healthy within 60s of restart — aborting." >&2
  "${DC[@]}" logs --tail=50 odoo >&2
  exit 1; }

docker restart "$nginx_cid" >/dev/null || {
  echo "ERROR: failed to restart nginx ($nginx_cid) — it may hold a stale odoo IP." >&2; exit 1; }

# Deterministic edge readiness (no blind sleep): any HTTP answer on :80 means
# nginx is serving again. Tenant-agnostic on purpose.
edge_ready=0
for _ in $(seq 1 15); do
  if curl -s -o /dev/null http://localhost/ 2>/dev/null; then edge_ready=1; break; fi
  sleep 1
done
[ "$edge_ready" = 1 ] || {
  echo "ERROR: the nginx edge did not answer on :80 within 15s of restart." >&2; exit 1; }

echo "✅ E2E tenants ready: e2eclienta (Pro) · e2eclientb (Basic) · e2eadmin"
