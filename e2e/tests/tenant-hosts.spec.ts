import { test, expect, APIRequestContext } from '@playwright/test';

/**
 * Tenant hosts under the local base domain: <tenant>.ncollection.localhost.
 *
 * Requests go to the edge on 127.0.0.1 with the Host header set, so this needs no DNS and no /etc/hosts entry on any
 * machine (CI included): the Host header IS the thing under test. What it proves, end to end through Nginx and Odoo:
 *   - a tenant host under the base domain reaches THAT tenant's database,
 *   - the Host header cannot be used to reach a different tenant's database,
 *   - an unknown tenant host reaches no database,
 *   - a production-style host is not served as a tenant by the local edge (no fallback to a production domain).
 * See docs/markdown/TENANT_URLS.md.
 */
const EDGE = 'http://127.0.0.1';
const BASE = 'ncollection.localhost';

const get = (ctx: APIRequestContext, host: string, path = '/web/login') =>
  ctx.get(`${EDGE}${path}`, { headers: { Host: host }, maxRedirects: 0 });

/** JSON-RPC authenticate against `db` through the given Host. Returns the session uid (null when refused). */
async function uidFor(ctx: APIRequestContext, host: string, db: string): Promise<number | null> {
  const resp = await ctx.post(`${EDGE}/web/session/authenticate`, {
    headers: { Host: host, 'Content-Type': 'application/json' },
    data: { jsonrpc: '2.0', method: 'call', params: { db, login: 'admin', password: process.env.E2E_ADMIN_PW || 'admin' } },
    maxRedirects: 0,
  });
  if (resp.status() !== 200) return null;
  const body = await resp.json().catch(() => ({}));
  return body?.result?.uid ?? null;
}

test.describe('tenant hosts under the local base domain', () => {
  test('<tenant>.ncollection.localhost opens that tenant\'s login page', async ({ playwright }) => {
    const ctx = await playwright.request.newContext();
    for (const db of ['e2eclienta', 'e2eclientb']) {
      const resp = await get(ctx, `${db}.${BASE}`);
      expect(resp.status(), `${db}.${BASE}`).toBe(200);
      expect(await resp.text()).toContain('name="login"');
    }
    await ctx.dispose();
  });

  test('the host selects the database: the tenant\'s own host authenticates, another tenant\'s does not', async ({ playwright }) => {
    const ctx = await playwright.request.newContext();
    expect(await uidFor(ctx, `e2eclienta.${BASE}`, 'e2eclienta'), 'own host, own database').toBeTruthy();
    // Manipulating the Host header or the database name cannot reach the other tenant's database
    expect(await uidFor(ctx, `e2eclienta.${BASE}`, 'e2eclientb'), 'host A, database B').toBeNull();
    expect(await uidFor(ctx, `e2eclientb.${BASE}`, 'e2eclienta'), 'host B, database A').toBeNull();
    await ctx.dispose();
  });

  test('an unknown tenant host reaches no database', async ({ playwright }) => {
    const ctx = await playwright.request.newContext();
    const resp = await get(ctx, `nosuchtenant.${BASE}`);
    expect(resp.status()).not.toBe(200);
    expect(await resp.text()).not.toContain('name="login"');
    expect(await uidFor(ctx, `nosuchtenant.${BASE}`, 'e2eclienta')).toBeNull();
    await ctx.dispose();
  });

  test('a production-style host is not served as a tenant by the local edge', async ({ playwright }) => {
    const ctx = await playwright.request.newContext();
    const resp = await get(ctx, 'e2eclienta.ncollectionerp.com');
    expect(resp.status()).toBe(302);                                  // sent to the platform host, never to a tenant
    expect(resp.headers()['location'] || '').toContain(BASE);
    await ctx.dispose();
  });
});
