import { test, expect, Page } from '@playwright/test';
import { activateLanguage, callKw, loginViaRpc, NAVBAR, TENANTS } from '../fixtures/tenants';

/**
 * Release smoke: walk the main screens repeatedly, in both languages, across reloads and a logout/login, and
 * assert the browser never reports an uncaught promise / IndexedDB error (the "database connection is
 * closing" InvalidStateError of the mail unread-badge store) or an Odoo error dialog.
 */
const TENANT = 'e2eclienta' as const;
const SCREENS = [
  'ncollection_core.action_ncollection_dashboard',           // Summary
  'sale.action_quotations_with_onboarding',                  // Quotations (attachment column)
  'sale.action_orders',                                      // Sales orders
  'account.action_move_out_invoice_type',                    // Invoices
  'ncollection_account_reports.action_nc_report_hub',        // Reports hub
  'ncollection_account_reports.action_nc_view_balance_sheet',
  'ncollection_account_reports.action_nc_view_profit_loss',
  'ncollection_core.action_nc_launcher_app_manager',          // Settings > Applications
  'ncollection_account_core.action_nc_accounting_defaults',   // Settings > Accounting Defaults
  'ncollection_stock_barcode.action_nc_barcode_scanner',      // Barcode scanner
];
const BAD = /IndexedDB|InvalidStateError|UncaughtPromiseError|connection is closing/i;

function watch(page: Page) {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error' && BAD.test(m.text())) errors.push(m.text()); });
  return errors;
}

/**
 * A SCSS compile error does not throw: Odoo serves a stub bundle that paints "A css error occured, using an old style
 * to render this page" over the page. Neither a pageerror listener nor an element probe sees that, so look for it.
 */
async function expectStylesheetsCompiled(page: Page, where: string) {
  const banner = await page.evaluate(() => getComputedStyle(document.body, '::before').content);
  expect(banner, `${where}: css error banner`).not.toMatch(/css error/i);
  const bad = await page.evaluate(async () => {
    const out: string[] = [];
    for (const link of Array.from(document.querySelectorAll<HTMLLinkElement>('link[rel=stylesheet][href*="/web/assets/"]'))) {
      const text = await (await fetch(link.href)).text();
      if (text.includes('css_error_message') || text.includes('A css error occured')) out.push(link.href);
    }
    return out;
  });
  expect(bad, `${where}: stylesheet bundle compiled with an error`).toEqual([]);
}

async function walk(page: Page, errors: string[]) {
  for (const xmlid of SCREENS) {
    await page.goto(`${TENANTS[TENANT]}/odoo/action-${xmlid}`, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector(NAVBAR, { timeout: 60_000 });
    await page.waitForTimeout(800);                          // let the unread-badge write and the view settle
    await expect(page.locator('.o_error_dialog')).toHaveCount(0);
    await expect(page.locator('.o_notification'), `${xmlid}: error notification`).toHaveCount(0);
    await expectStylesheetsCompiled(page, xmlid);
  }
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForSelector(NAVBAR, { timeout: 60_000 });
  expect(errors.filter((e) => BAD.test(e)), 'IndexedDB / uncaught promise errors').toEqual([]);
}

async function setLang(page: Page, lang: string) {
  await activateLanguage(page.request, TENANT, lang);
  const res = await callKw(page.request, TENANT, 'res.users', 'write', [[2], { lang }]);
  expect(res.error, JSON.stringify(res.error)).toBeUndefined();
}

test('screens, reload, language switch and logout/login raise no IndexedDB or uncaught errors', async ({ page }) => {
  test.setTimeout(8 * 60_000);
  const errors = watch(page);
  await loginViaRpc(page, TENANT);
  for (let round = 0; round < 2; round++) {
    await walk(page, errors);
  }
  try {
    await setLang(page, 'ar_001');
    await walk(page, errors);
    const dir = await page.evaluate(() => (window as any).odoo.loader.modules.get('@web/core/l10n/localization')?.localization?.direction);
    expect(dir).toBe('rtl');
  } finally {
    await setLang(page, 'en_US');                            // never leave the shared fixture user in Arabic
  }
  await walk(page, errors);

  // logout / login again, then once more through everything
  await page.goto(`${TENANTS[TENANT]}/web/session/logout`, { waitUntil: 'domcontentloaded' });
  await loginViaRpc(page, TENANT);
  await walk(page, errors);
  expect(errors.filter((e) => BAD.test(e))).toEqual([]);
});
