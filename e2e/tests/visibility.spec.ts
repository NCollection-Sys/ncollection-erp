import { test, expect } from '@playwright/test';
import { authenticate, menuVisible, callKw } from '../fixtures/tenants';

// P1-T09/T10 per-plan module gating: `biz` holds the Sales groups, so on the Pro
// plan (e2eclienta, licensed) the Sales app is present and usable, while on the
// Basic plan (e2eclientb, unlicensed) the module is gated — access is denied.
test.describe('module gating per plan (P1-T09/T10)', () => {
  test('Pro plan exposes Sales; Basic plan gates it', async ({ playwright }) => {
    const a = await playwright.request.newContext({ ignoreHTTPSErrors: true });
    await authenticate(a, 'e2eclienta', 'biz', 'demo1234');
    expect(await menuVisible(a, 'e2eclienta', 'Sales'), 'Pro plan should expose Sales').toBeTruthy();
    const okAccess = await callKw(a, 'e2eclienta', 'sale.order', 'search_count', [[]]);
    expect(okAccess.error, 'Pro plan should allow sale.order').toBeUndefined();
    await a.dispose();

    const b = await playwright.request.newContext({ ignoreHTTPSErrors: true });
    await authenticate(b, 'e2eclientb', 'biz', 'demo1234');
    const gated = await callKw(b, 'e2eclientb', 'sale.order', 'search_count', [[]]);
    expect(gated.error, 'Basic plan must gate sale.order').toBeDefined();
    await b.dispose();
  });
});
