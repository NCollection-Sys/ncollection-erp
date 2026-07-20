import { test, expect } from '@playwright/test';
import { authenticate, callKw } from '../fixtures/tenants';

// P1-T10: `biz` has the Sales groups, so sale.order access is gated by the plan
// license — allowed on clienta (licensed), denied on clientb (unlicensed).
test.describe('license enforcement (P1-T10)', () => {
  test('clienta biz reads sale.order; clientb biz is denied (unlicensed)', async ({ playwright }) => {
    const a = await playwright.request.newContext({ ignoreHTTPSErrors: true });
    await authenticate(a, 'e2eclienta', 'biz', 'demo1234');
    const ok = await callKw(a, 'e2eclienta', 'sale.order', 'search_count', [[]]);
    expect(ok.error, `clienta (licensed) must read sale.order: ${JSON.stringify(ok.error)}`).toBeUndefined();
    await a.dispose();

    const b = await playwright.request.newContext({ ignoreHTTPSErrors: true });
    await authenticate(b, 'e2eclientb', 'biz', 'demo1234');
    const denied = await callKw(b, 'e2eclientb', 'sale.order', 'search_count', [[]]);
    expect(denied.error, 'clientb (unlicensed) must be denied sale.order').toBeDefined();
    await b.dispose();
  });
});
