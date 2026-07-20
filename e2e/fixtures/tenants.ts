import { APIRequestContext, Page, expect } from '@playwright/test';

/**
 * Per-tenant base URLs (routed by db_filter through the Nginx edge).
 *
 * FIXTURE NAMESPACE — the `e2e*` prefix is deliberate. The P1-T06 routing proof
 * owns `clienta`/`clientb`/`admin`, and `make routing-clean` drops those; sharing
 * one namespace meant either suite could destroy the other's fixtures. The names
 * must stay ALPHANUMERIC: `db_filter=^%d$` routes a subdomain to the database of
 * the SAME name, underscores are invalid in hostnames, and hyphens would need
 * Postgres quoting. Tenant key === subdomain === database name, always.
 */
export const TENANTS = {
  e2eclienta: 'http://e2eclienta.localhost',
  e2eclientb: 'http://e2eclientb.localhost',
  e2eadmin: 'http://e2eadmin.localhost',
} as const;
export type TenantKey = keyof typeof TENANTS;

/** The Odoo backend navbar — a stable marker that we are logged in. */
export const NAVBAR = '.o_main_navbar';

/**
 * Log in through the branded web login form. Success is confirmed by the server
 * redirect AWAY from /web/login (a failed login re-renders /web/login) — this
 * avoids waiting for the heavy web-client JS to paint the navbar, which is slow
 * and flaky on a workers=0 dev server.
 */
export async function login(
  page: Page,
  tenant: TenantKey,
  loginName = 'admin',
  password = 'admin',
): Promise<void> {
  await page.goto(`${TENANTS[tenant]}/web/login`, { waitUntil: 'domcontentloaded' });
  await page.fill('input[name="login"]', loginName);
  await page.fill('input[name="password"]', password);
  await Promise.all([
    page.waitForURL((url) => !url.pathname.startsWith('/web/login'), { timeout: 60_000 }),
    page.click('button[type="submit"]'),
  ]);
}

export async function logout(page: Page, tenant: TenantKey): Promise<void> {
  await page.goto(`${TENANTS[tenant]}/web/session/logout`, { waitUntil: 'domcontentloaded' });
}

/**
 * Assert the page (or the tenant's backend) is showing the login form — i.e.
 * NOT authenticated. Navigates to /web, which the server redirects to
 * /web/login when there is no session.
 */
export async function expectLoggedOut(page: Page, tenant: TenantKey): Promise<void> {
  // Visiting the backend with no session server-redirects (303) to the login
  // page. That redirect is the proof of logout — no DOM/JS-render wait needed.
  await page.goto(`${TENANTS[tenant]}/web`, { waitUntil: 'commit' });
  await page.waitForURL((url) => url.pathname.startsWith('/web/login'), { timeout: 30_000 });
}

// ---- JSON-RPC helpers (server-side guarantees) ----------------------------

/**
 * Authenticate against a tenant. `db` defaults to the tenant key because under
 * `db_filter=^%d$` they are always the same value — passing both invites them to
 * drift apart. It stays overridable so a test can deliberately send a MISMATCHED
 * database and assert the edge refuses it (the P1-T06 check-2 scenario).
 */
export async function authenticate(
  request: APIRequestContext,
  tenant: TenantKey,
  loginName = 'admin',
  password = 'admin',
  db: string = tenant,
) {
  return request.post(`${TENANTS[tenant]}/web/session/authenticate`, {
    headers: { 'Content-Type': 'application/json' },
    data: { jsonrpc: '2.0', method: 'call', params: { db, login: loginName, password } },
  });
}

/** Extract the session_id cookie value currently held by the request context. */
export async function sessionId(request: APIRequestContext): Promise<string | undefined> {
  const state = await request.storageState();
  return state.cookies.find((c) => c.name === 'session_id')?.value;
}

/** POST get_session_info to a tenant, optionally forcing a specific session cookie. */
export async function getSessionInfo(
  request: APIRequestContext,
  tenant: TenantKey,
  cookie?: string,
) {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (cookie) headers['Cookie'] = `session_id=${cookie}`;
  const res = await request.post(`${TENANTS[tenant]}/web/session/get_session_info`, {
    headers,
    data: { jsonrpc: '2.0', method: 'call', params: {} },
  });
  return res.json();
}

/**
 * Fetch the visibility-filtered menu tree for the current session and return
 * whether an app/menu with `name` is present. Uses `load_menus`, which applies
 * `ir.ui.menu._visible_menu_ids` (the P1-T09 hiding) — so this reflects real
 * per-plan / per-role visibility without any brittle DOM coupling.
 */
export async function menuVisible(
  request: APIRequestContext,
  tenant: TenantKey,
  name: string,
): Promise<boolean> {
  // ir.ui.menu.load_menus applies _visible_menu_ids (the P1-T09 hiding). The
  // result is a dict keyed by menu id; check the parsed objects (not a string
  // match — JSON.stringify has no spaces, the raw body does).
  const res = await callKw(request, tenant, 'ir.ui.menu', 'load_menus', [false]);
  const menus = (res.result ?? {}) as Record<string, { name?: string }>;
  return Object.values(menus).some((m) => m && typeof m === 'object' && m.name === name);
}

/** call_kw against a tenant (uses whatever session cookie the context holds). */
export async function callKw(
  request: APIRequestContext,
  tenant: TenantKey,
  model: string,
  method: string,
  args: unknown[] = [],
  kwargs: Record<string, unknown> = {},
) {
  const res = await request.post(`${TENANTS[tenant]}/web/dataset/call_kw`, {
    headers: { 'Content-Type': 'application/json' },
    data: { jsonrpc: '2.0', method: 'call', params: { model, method, args, kwargs } },
  });
  return res.json();
}
