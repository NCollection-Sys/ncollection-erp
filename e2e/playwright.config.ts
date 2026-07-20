import { defineConfig, devices } from '@playwright/test';

/**
 * NCollection ERP E2E (P1-T20). The suite drives the REAL multi-tenant stack
 * through the Nginx edge with db_filter ON (the P1-T06 routing overlay), so
 * `clienta.localhost` / `clientb.localhost` route to their own databases.
 *
 * Tenants are created by `scripts/setup_e2e_tenants.sh` (run it — or `npm run
 * setup` — before the suite). `globalSetup` fails fast with a clear message if
 * the stack/tenants are not ready. There is no baseURL on purpose: tests use
 * absolute per-tenant URLs (the whole point is cross-subdomain behaviour).
 */
export default defineConfig({
  testDir: './tests',
  globalSetup: './fixtures/global-setup',
  // Serial: the suite is small, and the browser journeys share one Odoo stack
  // (parallel workers contend on first-load asset compilation -> flake). Serial
  // is deterministic and still well under the 10-minute budget.
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  // Generous: the FIRST browser login per tenant triggers Odoo asset
  // compilation (~30s cold); subsequent navigations are fast.
  timeout: 90_000,
  expect: { timeout: 10_000 },
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    ignoreHTTPSErrors: true,
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
});
