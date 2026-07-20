import {
  chromium,
  request as playwrightRequest,
  APIRequestContext,
} from '@playwright/test';
import { TENANTS, authenticate, callKw, login, TenantKey } from './tenants';

const TENANT_KEYS: TenantKey[] = ['clienta', 'clientb'];

/**
 * Warm the stack before the suite so tests are deterministic:
 *  1. fail fast (helpful message) if the routing stack / tenants aren't up,
 *  2. prime each tenant's RPC caches (license/visibility are server-side @ormcache),
 *  3. compile each tenant's web-client assets ONCE in a real browser — the first
 *     login per tenant is slow (asset build on a workers=0 dev server), so doing
 *     it here up front makes every subsequent spec fast and stable.
 */
export default async function globalSetup(): Promise<void> {
  const probe = await playwrightRequest.newContext({ ignoreHTTPSErrors: true });
  try {
    const res = await probe.get(`${TENANTS.clienta}/web/login`, { timeout: 20_000 });
    if (!res.ok()) throw new Error(`clienta.localhost/web/login -> ${res.status()}`);
  } catch (err) {
    await probe.dispose();
    throw new Error(
      `E2E stack not ready (${(err as Error).message}).\n` +
        `Bring it up first:\n  make routing-up\n  bash e2e/scripts/setup_e2e_tenants.sh\n` +
        `and ensure *.localhost resolves to 127.0.0.1 (see e2e/README.md).`,
    );
  }
  await probe.dispose();

  // Prime RPC caches.
  for (const tenant of TENANT_KEYS) {
    const c = await playwrightRequest.newContext({ ignoreHTTPSErrors: true });
    try {
      await primeRpc(c, tenant);
    } catch {
      /* best-effort */
    }
    await c.dispose();
  }

  // Compile web-client assets once per tenant (the slow part).
  const browser = await chromium.launch();
  for (const tenant of TENANT_KEYS) {
    const page = await browser.newPage({ ignoreHTTPSErrors: true });
    try {
      await login(page, tenant);
    } catch {
      /* the spec asserts real login — this is only asset warming */
    }
    await page.close();
  }
  await browser.close();
}

async function primeRpc(c: APIRequestContext, tenant: TenantKey): Promise<void> {
  await authenticate(c, tenant, tenant, 'biz', 'demo1234');
  await callKw(c, tenant, 'ir.ui.menu', 'load_menus', [false]);
}
