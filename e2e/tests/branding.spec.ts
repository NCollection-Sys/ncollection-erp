import { test, expect } from '@playwright/test';
import { TENANTS } from '../fixtures/tenants';

// P1-T15: the public entry point must not expose "odoo" in its URL.
test('no "odoo" in the public entry URL', async ({ page }) => {
  await page.goto(`${TENANTS.clienta}/`);
  await expect(page).toHaveURL(/\/web(\/login|\?|$)/);
  expect(page.url().toLowerCase()).not.toContain('odoo');
});
