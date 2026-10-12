import { test, expect } from '@playwright/test';
import { login, NAVBAR, TENANTS } from '../fixtures/tenants';

/**
 * Regression guard for the client error
 *   UncaughtPromiseError > InvalidStateError: Failed to execute 'transaction' on 'IDBDatabase':
 *   The database connection is closing.
 *
 * Odoo's mail module keeps ONE long-lived idb-keyval connection for the unread-badge counter and
 * ignores the rejection of its writes. ncollection_branding re-opens that connection once when the
 * browser has closed it (static/src/js/mail_unread_store_patch.js). The browser closing the connection
 * is simulated by making the first badge transaction throw the exact DOMException the browser throws.
 */
const TENANT = 'e2eclienta' as const;

test('a closed unread-badge connection is reopened, not an uncaught promise error', async ({ page }) => {
  const pageErrors: string[] = [];
  page.on('pageerror', (e) => pageErrors.push(String(e)));

  await login(page, TENANT);
  await page.goto(`${TENANTS[TENANT]}/odoo?debug=1`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector(NAVBAR, { timeout: 60_000 });

  const result = await page.evaluate(async () => {
    const store = (window as any).odoo.__WOWL_DEBUG__.root.env.services['mail.store'];
    // Make the next transaction on the unread store fail like a closing connection, once.
    const original = IDBDatabase.prototype.transaction;
    let thrown = 0;
    IDBDatabase.prototype.transaction = function (this: IDBDatabase, ...args: any[]) {
      if (thrown === 0 && String(args[0]).includes('odoo-mail-unread-store')) {
        thrown += 1;
        throw new DOMException('The database connection is closing.', 'InvalidStateError');
      }
      return (original as any).apply(this, args);
    } as any;
    store.updateAppBadge();
    await new Promise((r) => setTimeout(r, 1500));
    IDBDatabase.prototype.transaction = original;
    // The value must have been written through the REOPENED connection.
    const idb = (window as any).idbKeyval;
    const reader = new idb.Store('odoo-mail-unread-db', 'odoo-mail-unread-store');
    const stored = await idb.get('unread', reader);
    return { thrown, stored, counter: store.globalCounter };
  });

  expect(result.thrown, 'the simulated closing connection must have been hit').toBe(1);
  expect(result.stored).toBe(result.counter);
  expect(pageErrors.filter((e) => e.includes('InvalidStateError'))).toEqual([]);
});

test('repeated navigation and reloads raise no uncaught promise errors', async ({ page }) => {
  const pageErrors: string[] = [];
  page.on('pageerror', (e) => pageErrors.push(String(e)));
  await login(page, TENANT);
  for (let i = 0; i < 3; i++) {
    for (const path of ['/odoo', '/odoo/action-ncollection_account_reports.action_nc_report_hub']) {
      await page.goto(`${TENANTS[TENANT]}${path}`, { waitUntil: 'domcontentloaded' });
      await page.waitForSelector(NAVBAR, { timeout: 60_000 });
    }
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForSelector(NAVBAR, { timeout: 60_000 });
  }
  expect(pageErrors).toEqual([]);
});
