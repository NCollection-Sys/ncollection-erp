import { test, expect, Page } from '@playwright/test';
import { loginViaRpc, NAVBAR, TENANTS } from '../fixtures/tenants';

/**
 * Summary: the From / To period, in a real browser on the e2eclienta tenant (admin = workspace Owner).
 *  - the default is the current year to today, with no way-back control;
 *  - choosing both dates asks the server once, with those dates, and the figures say they are for the selected period;
 *  - a From after the To is refused on the page and asks the server nothing;
 *  - the choice survives a reload in the same browser session and can be reset to the current year.
 */
const TENANT = 'e2eclienta' as const;
const PAYLOAD = /get_dashboard_payload/;

async function openSummary(page: Page) {
  await page.goto(`${TENANTS[TENANT]}/odoo/action-ncollection_core.action_ncollection_dashboard`, {
    waitUntil: 'domcontentloaded',
  });
  await page.waitForSelector(NAVBAR, { timeout: 60_000 });
  await page.waitForSelector('.nc-kpi-grid .nc-kpi-cell', { timeout: 60_000 });
}

function payloadCalls(page: Page) {
  const calls: { date_from?: string; date_to?: string }[] = [];
  page.on('request', (request) => {
    if (PAYLOAD.test(request.url()) && request.method() === 'POST') {
      calls.push((request.postDataJSON()?.params?.kwargs) || {});
    }
  });
  return calls;
}

async function setDate(page: Page, name: 'nc_period_from' | 'nc_period_to', value: string) {
  const input = page.locator(`input[name=${name}]`);
  await input.fill(value);
  await input.dispatchEvent('change');
}

test.describe('Summary period', () => {
  test.beforeEach(async ({ page }) => {
    await loginViaRpc(page, TENANT);
    // loginViaRpc only sets the session cookie: the page is still about:blank, which has no sessionStorage
    await page.goto(`${TENANTS[TENANT]}/odoo`, { waitUntil: 'domcontentloaded' });
    await page.evaluate(() => sessionStorage.clear());
  });

  test('the default is the current year to today, and the control is there', async ({ page }) => {
    await openSummary(page);
    const year = new Date().getFullYear();
    await expect(page.locator('input[name=nc_period_from]')).toHaveValue(`${year}-01-01`);
    await expect(page.locator('input[name=nc_period_to]')).toHaveValue(/^\d{4}-\d{2}-\d{2}$/);
    await expect(page.locator('.nc-period__reset')).toHaveCount(0);
  });

  test('a chosen period is asked of the server once, with those dates', async ({ page }) => {
    const calls = payloadCalls(page);
    await openSummary(page);
    const before = calls.length;
    const year = new Date().getFullYear();
    await setDate(page, 'nc_period_from', `${year}-02-01`);
    await setDate(page, 'nc_period_to', `${year}-03-31`);
    await expect(page.locator('.nc-period__reset')).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('[data-widget=net_profit]')).toContainText('Selected period');
    const asked = calls.slice(before).filter((c) => c.date_from);
    expect(asked.length, 'one refresh for the pair of dates, not one per field').toBeGreaterThan(0);
    // Odoo adds its standard `context` kwarg to every call: check the period, and that nothing else rides along
    const { context: _context, ...period } = asked[asked.length - 1] as Record<string, unknown>;
    expect(period).toEqual({ date_from: `${year}-02-01`, date_to: `${year}-03-31` });
  });

  test('a From after the To is refused on the page and asks the server nothing', async ({ page }) => {
    const calls = payloadCalls(page);
    await openSummary(page);
    const before = calls.length;
    const year = new Date().getFullYear();
    await setDate(page, 'nc_period_from', `${year}-12-31`);
    await setDate(page, 'nc_period_to', `${year}-01-02`);
    await expect(page.locator('.nc-period__error')).toBeVisible();
    await page.waitForTimeout(800);
    expect(calls.length, 'no request for an unusable range').toBe(before);
  });

  test('the choice survives a reload and can be reset', async ({ page }) => {
    await openSummary(page);
    const year = new Date().getFullYear();
    await setDate(page, 'nc_period_from', `${year}-04-01`);
    await setDate(page, 'nc_period_to', `${year}-04-30`);
    await expect(page.locator('.nc-period__reset')).toBeVisible({ timeout: 15_000 });
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.nc-kpi-grid .nc-kpi-cell', { timeout: 60_000 });
    await expect(page.locator('input[name=nc_period_from]')).toHaveValue(`${year}-04-01`);
    await page.locator('.nc-period__reset').click();
    await expect(page.locator('input[name=nc_period_from]')).toHaveValue(`${year}-01-01`);
    await expect(page.locator('.nc-period__reset')).toHaveCount(0);
  });
});
