import { test } from '@playwright/test';
import { login, logout, expectLoggedOut, TenantKey } from '../fixtures/tenants';

// Login/logout works independently per tenant subdomain.
for (const tenant of ['e2eclienta', 'e2eclientb'] as TenantKey[]) {
  test(`${tenant}: login then logout`, async ({ page }) => {
    await login(page, tenant);            // asserts the server redirect off /web/login
    await logout(page, tenant);
    await expectLoggedOut(page, tenant);  // /web -> login form when no session
  });
}
