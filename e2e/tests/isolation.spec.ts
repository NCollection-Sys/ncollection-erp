import { test, expect } from '@playwright/test';
import { TENANTS, authenticate, sessionId, getSessionInfo } from '../fixtures/tenants';

test.describe('tenant session isolation (P1-T06 guarantee)', () => {
  // Browser: a tenant's backend requires its OWN session — visiting it without
  // one lands on the login page (a session from another tenant never applies,
  // since cookies are host-scoped). The stronger cross-session rejection is
  // proven at the server layer below.
  test('browser: the backend requires a per-tenant session (redirects to login)', async ({ page }) => {
    await page.goto(`${TENANTS.clientb}/web`, { waitUntil: 'commit' });
    await page.waitForURL((url) => url.pathname.startsWith('/web/login'), { timeout: 30_000 });
  });

  // Server: a session is DB-scoped — forcing tenant A's session cookie onto
  // tenant B is rejected (uid null), and vice-versa. (Port of verify_routing.sh
  // check #3.)
  test('server: a session is DB-scoped and rejected on the other tenant (both ways)', async ({ playwright }) => {
    const a = await playwright.request.newContext({ ignoreHTTPSErrors: true });
    await authenticate(a, 'clienta', 'clienta');
    const sidA = await sessionId(a);
    expect(sidA, 'clienta must issue a session').toBeTruthy();
    const aOnB = await getSessionInfo(a, 'clientb', sidA);
    expect(aOnB.result?.uid ?? null, 'clienta session must be invalid on clientb').toBeNull();
    await a.dispose();

    const b = await playwright.request.newContext({ ignoreHTTPSErrors: true });
    await authenticate(b, 'clientb', 'clientb');
    const sidB = await sessionId(b);
    const bOnA = await getSessionInfo(b, 'clienta', sidB);
    expect(bOnA.result?.uid ?? null, 'clientb session must be invalid on clienta').toBeNull();
    await b.dispose();
  });
});
