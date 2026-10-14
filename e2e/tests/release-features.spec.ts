import { test, expect, Page } from '@playwright/test';
import { callKw, loginViaRpc, NAVBAR, TENANTS } from '../fixtures/tenants';

/**
 * Release features, in a real browser, on the e2eclienta tenant (admin = workspace Owner):
 *  - Summary: shared KPI cards, interactive statement amounts, KPI drill-downs, no CSS banner;
 *  - Home launcher: hide/show an application without touching access;
 *  - Settings > Applications and Accounting Defaults render;
 *  - Barcode scanner: scan a receipt end to end;
 *  - Arabic (RTL) for the same screens. The shared fixture user is always put back to English.
 */
const TENANT = 'e2eclienta' as const;
const BAD = /IndexedDB|InvalidState|UncaughtPromiseError|connection is closing/i;

function watch(page: Page) {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error' && BAD.test(m.text())) errors.push(m.text()); });
  return errors;
}

async function open(page: Page, xmlid: string) {
  await page.goto(`${TENANTS[TENANT]}/odoo/action-${xmlid}`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector(NAVBAR, { timeout: 60_000 });
}

async function noCssError(page: Page, where: string) {
  const banner = await page.evaluate(() => getComputedStyle(document.body, '::before').content);
  expect(banner, `${where}: css error banner`).not.toMatch(/css error/i);
  await expect(page.locator('.o_notification'), `${where}: notification`).toHaveCount(0);
}

async function rpc(page: Page, model: string, method: string, args: unknown[], kwargs: Record<string, unknown> = {}) {
  const res = await callKw(page.request, TENANT, model, method, args, kwargs);
  expect(res.error, JSON.stringify(res.error)).toBeUndefined();
  return res.result;
}

async function setLang(page: Page, lang: string) {
  await rpc(page, 'res.users', 'write', [[2], { lang }]);
}

test.describe.configure({ mode: 'serial' });

test.describe('Summary', () => {
  test.beforeAll(async ({ playwright }) => {
    // one posted entry so the statements and figures have something to open
    const request = await playwright.request.newContext();
    await request.post(`${TENANTS[TENANT]}/web/session/authenticate`, {
      data: { jsonrpc: '2.0', params: { db: TENANT, login: 'admin', password: 'admin' } } });
    const call = async (model: string, method: string, args: unknown[], kwargs: Record<string, unknown> = {}) =>
      (await (await request.post(`${TENANTS[TENANT]}/web/dataset/call_kw`, {
        headers: { 'Content-Type': 'application/json' },
        data: { jsonrpc: '2.0', method: 'call', params: { model, method, args, kwargs } } })).json()).result;
    const account = async (type: string) =>
      (await call('account.account', 'search', [[['account_type', '=', type]]], { limit: 1 }))[0];
    const journal = (await call('account.journal', 'search', [[['type', '=', 'general']]], { limit: 1 }))[0];
    const [recv, income, expense, cash] = [await account('asset_receivable'), await account('income'),
      await account('expense'), await account('asset_cash')];
    if (journal && recv && income && expense && cash) {
      const post = async (debit: number, credit: number, amount: number) => {
        const id = await call('account.move', 'create', [{ move_type: 'entry', journal_id: journal, date: new Date().toISOString().slice(0, 10),
          line_ids: [[0, 0, { account_id: debit, debit: amount, credit: 0 }], [0, 0, { account_id: credit, debit: 0, credit: amount }]] }]);
        await call('account.move', 'action_post', [[id]]);
      };
      await post(recv, income, 5000);
      await post(expense, cash, 1200);
    }
    await request.dispose();
  });

  test('shared cards, Net Profit / Expenses, and drill-downs that open real journal items', async ({ page }) => {
    const errors = watch(page);
    await loginViaRpc(page, TENANT);
    await open(page, 'ncollection_core.action_ncollection_dashboard');
    await expect(page.locator('.nc-dashboard__title')).toHaveText('Summary', { timeout: 30_000 });
    await noCssError(page, 'summary');

    const cards = page.locator('.nc-kpi-cell');
    expect(await cards.count()).toBeGreaterThanOrEqual(4);
    await expect(page.locator('.nc-kpi-cell[data-widget="net_profit"] .nc-kpi-card__value')).not.toBeEmpty();
    await expect(page.locator('.nc-kpi-cell[data-widget="expenses"]')).toBeVisible();
    // compact, not giant: a KPI card is a few lines tall
    const box = await page.locator('.nc-kpi-cell').first().boundingBox();
    expect(box!.height).toBeLessThan(140);

    // a drillable card is a real button and opens exactly its list
    const cash = page.locator('.nc-kpi-cell[data-widget="cash_bank"] button.nc-kpi-card');
    await expect(cash).toBeVisible();
    await cash.click();
    await expect(page.locator('.o_list_renderer, .o_list_view').first()).toBeVisible({ timeout: 30_000 });
    await expect(page.locator('.o_control_panel').first()).toContainText(/Cash/i);
    await page.goBack();
    await page.waitForSelector('.nc-dashboard', { timeout: 30_000 });

    // a statement: expand an account group, click an amount -> the journal items of those accounts
    const statement = page.locator('.nc-statement-cell[data-widget="balance_sheet_summary"]');
    await expect(statement).toBeVisible();
    const toggle = statement.locator('.nc-statement-row__toggle').first();
    if (await toggle.count()) {
      await toggle.click();
      // web-first: the expanded rows are drawn on the next render, not inside the click
      await expect(statement.locator('.nc-statement-row--l2').first()).toBeVisible();
    }
    const amount = statement.locator('.nc-statement-row__amount').first();
    await expect(amount).toBeVisible();
    await amount.click();
    await expect(page.locator('.o_list_renderer').first()).toBeVisible({ timeout: 30_000 });
    await expect(page.locator('.o_control_panel').first()).toContainText(/Journal Items/i);
    expect(errors.filter((e) => BAD.test(e))).toEqual([]);
  });

  test('Arabic: the same page mirrors, with the shared cards, and English is restored', async ({ page }) => {
    const errors = watch(page);
    await loginViaRpc(page, TENANT);
    try {
      await setLang(page, 'ar_001');
      await open(page, 'ncollection_core.action_ncollection_dashboard');
      await expect(page.locator('.nc-kpi-card').first()).toBeVisible({ timeout: 30_000 });
      await noCssError(page, 'summary ar');
      const dir = await page.evaluate(() => (window as any).odoo.loader.modules.get('@web/core/l10n/localization')?.localization?.direction);
      expect(dir).toBe('rtl');
      // the mirrored selectors (.o_rtl &) hang on <body>; a drillable card's chevron is flipped
      expect(await page.evaluate(() => document.body.classList.contains('o_rtl'))).toBe(true);
      const chevron = page.locator('.nc-kpi-card__go').first();
      if (await chevron.count()) {
        expect(await chevron.evaluate((el) => getComputedStyle(el).transform)).not.toBe('none');
      }
      // no horizontal page scroll in either direction
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)).toBe(true);
    } finally {
      await setLang(page, 'en_US');
    }
    expect(errors.filter((e) => BAD.test(e))).toEqual([]);
  });

  for (const [w, h] of [[1920, 1080], [1366, 768], [1024, 768], [768, 1024], [480, 900]] as const) {
    test(`Summary has no horizontal overflow at ${w}x${h}`, async ({ page }) => {
      await page.setViewportSize({ width: w, height: h });
      await loginViaRpc(page, TENANT);
      await open(page, 'ncollection_core.action_ncollection_dashboard');
      await expect(page.locator('.nc-kpi-card').first()).toBeVisible({ timeout: 30_000 });
      const overflow = await page.evaluate(() => {
        const el = document.querySelector('.nc-dashboard') as HTMLElement;
        return el.scrollWidth - el.clientWidth;
      });
      expect(overflow, `${w}px`).toBeLessThanOrEqual(1);
    });
  }
});

test.describe('Applications and defaults', () => {
  test('Settings > Applications lists the plan, hiding an app removes only its Home tile', async ({ page }) => {
    const errors = watch(page);
    await loginViaRpc(page, TENANT);
    await rpc(page, 'ncollection.launcher.app', 'action_open_manager', []);
    const rows = await rpc(page, 'ncollection.launcher.app', 'search_read', [[]], { fields: ['module_name', 'state', 'visible'] });
    const by = Object.fromEntries(rows.map((r: any) => [r.module_name, r]));
    expect(by.stock.state).toBe('active');
    expect(by.fleet.state).toBe('locked');                  // a Community app this plan does not include
    for (const app of ['point_of_sale', 'mrp', 'hr_recruitment', 'fleet', 'im_livechat', 'hr_attendance', 'mass_mailing_sms']) {
      expect(by[app], app).toBeTruthy();
    }

    await open(page, 'ncollection_core.action_nc_launcher_app_manager');
    await expect(page.locator('.o_list_renderer')).toBeVisible({ timeout: 30_000 });
    await noCssError(page, 'applications');

    const tile = () => page.locator('.o_nc_home__card[data-xmlid^="stock."]');
    await page.goto(`${TENANTS[TENANT]}/odoo`, { waitUntil: 'domcontentloaded' });
    await expect(page.locator('.o_nc_home__card').first()).toBeVisible({ timeout: 30_000 });
    await expect(tile()).toHaveCount(1);
    try {
      await rpc(page, 'ncollection.launcher.app', 'write', [[by.stock.id], { visible: false }]);
      await page.reload({ waitUntil: 'domcontentloaded' });
      await expect(page.locator('.o_nc_home__card').first()).toBeVisible({ timeout: 30_000 });
      await expect(tile()).toHaveCount(0);                                       // hidden on Home ...
      await open(page, 'stock.stock_picking_type_action');                       // ... but Inventory still works
      await expect(page.locator(NAVBAR)).toBeVisible();
      await noCssError(page, 'inventory still reachable');
    } finally {
      await rpc(page, 'ncollection.launcher.app', 'write', [[by.stock.id], { visible: true }]);
    }
    expect(errors.filter((e) => BAD.test(e))).toEqual([]);
  });

  test('Accounting Defaults opens the active company form', async ({ page }) => {
    await loginViaRpc(page, TENANT);
    await open(page, 'ncollection_account_core.action_nc_accounting_defaults');
    await expect(page.locator('.o_form_view')).toBeVisible({ timeout: 30_000 });
    await expect(page.locator('.o_form_view')).toContainText(/Default receivable account/i);
    await expect(page.locator('.o_form_view')).toContainText(/Change Period/i);
    await noCssError(page, 'accounting defaults');
  });
});

test.describe('Barcode scanner', () => {
  test('scan a receipt: product by barcode, quantity, validate', async ({ page }) => {
    const errors = watch(page);
    await loginViaRpc(page, TENANT);
    const barcode = `629104${Date.now().toString().slice(-7)}`;
    const product = await rpc(page, 'product.product', 'create', [{ name: 'E2E Scan Widget', is_storable: true, barcode }]);
    const wh = (await rpc(page, 'stock.warehouse', 'search_read', [[]], { fields: ['in_type_id', 'lot_stock_id'], limit: 1 }))[0];
    const supplier = (await rpc(page, 'stock.location', 'search', [[['usage', '=', 'supplier']]], { limit: 1 }))[0];
    const picking = await rpc(page, 'stock.picking', 'create', [{
      picking_type_id: wh.in_type_id[0], location_id: supplier, location_dest_id: wh.lot_stock_id[0],
      move_ids: [[0, 0, { product_id: product, product_uom_qty: 3, product_uom: 1, location_id: supplier, location_dest_id: wh.lot_stock_id[0] }]] }]);
    await rpc(page, 'stock.picking', 'action_confirm', [[picking]]);
    const name = (await rpc(page, 'stock.picking', 'read', [[picking], ['name']]))[0].name as string;

    await open(page, 'ncollection_stock_barcode.action_nc_barcode_scanner');
    await expect(page.locator('.nc-barcode')).toBeVisible({ timeout: 30_000 });
    await noCssError(page, 'scanner');
    const input = page.locator('.nc-barcode__input');
    await expect(input).toBeFocused();

    await input.fill(name);                       // a hardware scanner types the code and presses Enter
    await input.press('Enter');
    await expect(page.locator('.nc-barcode__picking')).toBeVisible({ timeout: 20_000 });
    await expect(page.locator('.nc-barcode__line')).toContainText('E2E Scan Widget');

    for (let i = 0; i < 3; i += 1) {
      await input.fill(barcode);
      await input.press('Enter');
    }
    await expect(page.locator('.nc-barcode__count').first()).toContainText('3');
    await expect(page.locator('.nc-barcode__line.is-complete')).toHaveCount(1);
    await input.fill('NOT-A-REAL-CODE');
    await input.press('Enter');
    await expect(page.locator('.nc-barcode__message--danger')).toBeVisible();

    await page.locator('.nc-barcode__validate').click();
    await expect(page.locator('.nc-barcode__operations')).toBeVisible({ timeout: 20_000 });
    const state = (await rpc(page, 'stock.picking', 'read', [[picking], ['state']]))[0].state;
    expect(state).toBe('done');
    expect(errors.filter((e) => BAD.test(e))).toEqual([]);
  });

  test('the scanner mirrors in Arabic and English is restored', async ({ page }) => {
    await loginViaRpc(page, TENANT);
    try {
      await setLang(page, 'ar_001');
      await open(page, 'ncollection_stock_barcode.action_nc_barcode_scanner');
      await expect(page.locator('.nc-barcode')).toBeVisible({ timeout: 30_000 });
      const dir = await page.evaluate(() => (window as any).odoo.loader.modules.get('@web/core/l10n/localization')?.localization?.direction);
      expect(dir).toBe('rtl');
      await noCssError(page, 'scanner ar');
      // the scan input stays left-to-right: barcodes are Latin
      expect(await page.locator('.nc-barcode__input').getAttribute('dir')).toBe('ltr');
    } finally {
      await setLang(page, 'en_US');
    }
  });
});
