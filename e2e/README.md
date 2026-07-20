# NCollection ERP — End-to-End Tests (P1-T20)

Playwright suite that drives the **real** multi-tenant Odoo stack through the Nginx edge
and continuously enforces the platform's core guarantees:

| Guarantee | Spec | Ticket |
|---|---|---|
| Login / logout per subdomain | `auth.spec.ts` | P1-T06 / T03 |
| Session isolation (DB-scoped sessions, rejected cross-tenant) | `isolation.spec.ts` | P1-T06 |
| Per-plan module visibility (Pro shows Sales, Basic hides it) | `visibility.spec.ts` | P1-T09 |
| License enforcement (unlicensed model → `AccessError`) | `license.spec.ts` | P1-T10 |
| Owner-only menus (admin sees Settings, regular user doesn't) | `roles.spec.ts` | P1-T11 |
| No "Odoo" brand string in the public entry URL | `branding.spec.ts` | P1-T14/T15 |

The suite targets tenants by subdomain — `clienta.localhost` / `clientb.localhost` /
`admin.localhost` — each routed to its **own** database by `db_filter` at the Nginx edge.
There is **no `baseURL`** on purpose: cross-subdomain behaviour is the whole point.

## The two tenants (different plans → divergent behaviour)

| Tenant | Plan | Installed | `allowed_module_names` | Sales app |
|---|---|---|---|---|
| `clienta` | Pro | `crm`, `sale` | `crm,sale` | visible + usable |
| `clientb` | Basic | `crm`, `sale` | `crm` | installed but **hidden + access-blocked** |
| `admin` | — | `base` | — | platform / routing target |

`clientb` has `sale` **installed but unlicensed**, which is exactly what P1-T09 (menu
hidden) and P1-T10 (access blocked) enforce. Both tenants also get a non-system business
user **`biz` / `demo1234`** holding the Sales groups — enforcement is bypassed for system
users (the Owner/admin), so the journeys probe as `biz` to exercise the *plan* gate.

## Run it locally

Prerequisites: Docker (the dev stack), Node 20+, and the aggregated OCA tree.

```bash
# 0. one-time: *.localhost must resolve to loopback (Chromium + Node both honour /etc/hosts)
echo "127.0.0.1 clienta.localhost clientb.localhost admin.localhost" | sudo tee -a /etc/hosts

# 1. bring up the routing overlay (db_filter ON, workers=0) and create the tenants
make oca                         # aggregate pinned OCA repos (first run only)
make routing-up
bash e2e/scripts/setup_e2e_tenants.sh

# 2. install and run
cd e2e
npm ci
npx playwright install chromium
npx playwright test              # ~15s, 8 tests, serial
npm run report                   # open the HTML report
```

`globalSetup` fails fast with a clear message if the stack/tenants are not up. It also
primes each tenant's server-side caches and compiles the web-client assets once so the
specs are deterministic.

## Add a journey

1. Add `e2e/tests/<name>.spec.ts`.
2. Reuse the helpers in `fixtures/tenants.ts` — do **not** hand-roll URLs, logins, or RPC:
   - `login(page, tenant, user?, pw?)` / `logout()` / `expectLoggedOut()` — browser flows.
   - `authenticate() / sessionId() / getSessionInfo() / callKw()` — server-side JSON-RPC.
   - `menuVisible(request, tenant, name)` — applies the real P1-T09 visibility filter.
3. Assert at the **enforcement layer**. The platform enforces isolation/visibility/license
   server-side (Standing Rule 4), so an HTTP-redirect or RPC assertion is more faithful —
   and far less flaky — than scraping the rendered web client. Keep any DOM coupling thin
   and centralized in `fixtures/`.
4. No arbitrary `waitForTimeout`. Use Playwright auto-waiting and server-redirect waits
   (see `login()` / `expectLoggedOut()` for the pattern).

## Runtime contract — the websocket port (read before touching the stack)

Odoo serves the realtime **bus** on a port that depends on worker mode:

- `workers=0` (the routing overlay, threaded) → bus on **8069**
- `workers>0` (gevent) → bus on the evented port **8072**

The dev Nginx (`nginx/conf.d/ncollection.dev.conf`) routes `/websocket` → `odoo:8069`. So the
suite runs the routing overlay at **workers=0** — bus and Nginx agree, and the web client
never stalls on a bus it can't reach. **Do not** run this stack with `--workers>0`: the bus
moves to 8072 while Nginx still points at 8069 →
`RuntimeError: Couldn't bind the websocket. Is the connection opened on the evented port (8072)?`
and hung workers.

If a future journey needs the full web client **under concurrent load** (where workers>0 is
worth it), pair it with a dedicated `nginx/conf.d/ncollection.e2e.conf` that routes
`/websocket` → `odoo:8072` (mirrors the P1-T03 prod bus contract) — new files only, no edit
to the shipped dev/prod configs.

## CI

`.github/workflows/e2e.yml` runs this suite on every PR to `develop`: aggregate OCA →
map `*.localhost` → bring up the routing overlay → create tenants → `playwright test`.
The Playwright report + traces upload as an artifact on failure. Hard cap 15 min; target
< 10.

## Regression net (proven)

The suite goes **red** when a guarantee is removed — e.g. granting `clientb` the `sale`
license (`allowed_module_names = "crm,sale"`) reddens `license.spec` and `visibility.spec`;
removing `db_filter` reddens `isolation.spec`. That is the point: it fails loudly on a
deliberate isolation/visibility regression.
