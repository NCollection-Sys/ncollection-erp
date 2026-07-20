import { test, expect } from '@playwright/test';
import { authenticate, callKw } from '../fixtures/tenants';

// P1-T10: `biz` has the Sales groups, so sale.order access is gated by the plan
// license — allowed on clienta (licensed), denied on clientb (unlicensed).
test.describe('license enforcement (P1-T10)', () => {
  test('clienta biz reads sale.order; clientb biz is denied (unlicensed)', async ({ playwright }) => {
    const a = await playwright.request.newContext({ ignoreHTTPSErrors: true });
    await authenticate(a, 'clienta', 'clienta', 'biz', 'demo1234');
    const ok = await callKw(a, 'clienta', 'sale.order', 'search_count', [[]]);
    expect(ok.error, `clienta (licensed) must read sale.order: ${JSON.stringify(ok.error)}`).toBeUndefined();
    await a.dispose();

    const b = await playwright.request.newContext({ ignoreHTTPSErrors: true });
    await authenticate(b, 'clientb', 'clientb', 'biz', 'demo1234');
    const denied = await callKw(b, 'clientb', 'sale.order', 'search_count', [[]]);
    expect(denied.error, 'clientb (unlicensed) must be denied sale.order').toBeDefined();
    await b.dispose();
  });
});
