import { test, expect } from '@playwright/test';
import { authenticate, menuVisible } from '../fixtures/tenants';

// P1-T11 owner-only menus: the admin (Owner/system) sees Settings; a regular
// business user does not. A role/owner menu spot check on e2eclienta.
test.describe('owner-only menu gating (P1-T11) — e2eclienta', () => {
  test('admin sees Settings; a regular user does not', async ({ playwright }) => {
    const owner = await playwright.request.newContext({ ignoreHTTPSErrors: true });
    await authenticate(owner, 'e2eclienta', 'admin', 'admin');
    expect(await menuVisible(owner, 'e2eclienta', 'Settings'), 'owner should see Settings').toBeTruthy();
    await owner.dispose();

    const user = await playwright.request.newContext({ ignoreHTTPSErrors: true });
    await authenticate(user, 'e2eclienta', 'biz', 'demo1234');
    expect(await menuVisible(user, 'e2eclienta', 'Settings'), 'regular user must NOT see Settings').toBeFalsy();
    await user.dispose();
  });
});
