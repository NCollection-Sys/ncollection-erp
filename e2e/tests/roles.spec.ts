import { test, expect } from '@playwright/test';
import { authenticate, menuVisible } from '../fixtures/tenants';

// P1-T11 owner-only menus: the admin (Owner/system) sees Settings; a regular
// business user does not. A role/owner menu spot check on clienta.
test.describe('owner-only menu gating (P1-T11) — clienta', () => {
  test('admin sees Settings; a regular user does not', async ({ playwright }) => {
    const owner = await playwright.request.newContext({ ignoreHTTPSErrors: true });
    await authenticate(owner, 'clienta', 'clienta', 'admin', 'admin');
    expect(await menuVisible(owner, 'clienta', 'Settings'), 'owner should see Settings').toBeTruthy();
    await owner.dispose();

    const user = await playwright.request.newContext({ ignoreHTTPSErrors: true });
    await authenticate(user, 'clienta', 'clienta', 'biz', 'demo1234');
    expect(await menuVisible(user, 'clienta', 'Settings'), 'regular user must NOT see Settings').toBeFalsy();
    await user.dispose();
  });
});
