import { test, expect, Page } from '@playwright/test';
import { callKw, loginViaRpc, TENANTS } from '../fixtures/tenants';

/**
 * Product branding and the company stamp configured from Accounting (e2eclienta; admin = workspace Owner).
 *  - the PRODUCT identity (name, favicon, logos) is ONE record, configured in a Product Branding form, and every surface
 *    reads it: the sign-in page, the password-reset page, the browser tab, an error page. Nothing says "Odoo".
 *  - the COMPANY brand (logo, stamp) is a different thing and is untouched by it.
 *  - the favicon is versioned in its URL, so a change is never served from a cache; SVG / non-image files are refused.
 *  - Accounting > Configuration > Document Stamps is the entry for the invoice stamp; an ordinary user cannot read the
 *    stamp image over /web/image.
 * Synthetic data only: a 10x10 PNG, a made-up user. The branding record is restored after every test.
 */
const TENANT = 'e2eclienta' as const;
const BASE = TENANTS[TENANT];
const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAYAAACNMs+9AAAAFUlEQVR42mP8z8BQz0AEYBxVSF+FABJADveWkH6oAAAAAElFTkSuQmCC';
const SVG = Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>').toString('base64');

function collectErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  return errors;
}

async function brandId(page: Page): Promise<number> {
  const rows = (await callKw(page.request, TENANT, 'ncollection.product.branding', 'search_read', [[]], { fields: ['id'], limit: 1 })).result;
  return rows[0].id;
}

async function restore(page: Page): Promise<void> {
  const id = await brandId(page);
  await callKw(page.request, TENANT, 'ncollection.product.branding', 'write',
    [[id], { name: 'NCollection ERP', short_name: 'NCollection', favicon: false, logo: false, login_logo: false, admin_logo: false }]);
}

/** Visible text of an HTML page fetched with no session: scripts and styles removed. */
async function anonymousPage(page: Page, path: string): Promise<{ title: string; text: string; icons: string[]; status: number }> {
  const res = await page.context().browser()!.newContext().then(async (ctx) => {
    const r = await ctx.request.get(`${BASE}${path}`);
    const html = await r.text();
    await ctx.close();
    return { status: r.status(), html };
  });
  const title = (res.html.match(/<title>([^<]*)<\/title>/i) || [, ''])[1] as string;
  const icons = [...res.html.matchAll(/<link[^>]+rel="[^"]*icon[^"]*"[^>]*>/gi)].map((m) => m[0]);
  const text = res.html.replace(/<(script|style)[\s\S]*?<\/\1>/gi, '').replace(/<[^>]+>/g, ' ');
  return { title, text, icons, status: res.status };
}

test.describe('Product branding', () => {
  test.beforeEach(async ({ page }) => {
    await loginViaRpc(page, TENANT);
  });
  test.afterEach(async ({ page }) => {
    await restore(page);
  });

  test('the Product Branding form changes the name and the favicon everywhere, and the favicon URL is versioned', async ({ page }) => {
    const errors = collectErrors(page);
    const id = await brandId(page);
    await page.goto(`${BASE}/odoo/action-ncollection_branding.action_product_branding`, { waitUntil: 'domcontentloaded' });
    const name = page.locator('.o_field_widget[name=name] input');
    await name.waitFor({ timeout: 90_000 });
    await expect(page.locator('.o_form_view')).toContainText('configured on the company, not here');
    const first = (await anonymousPage(page, '/web/login')).icons.join(' ');
    await name.fill('Acme Workspace');
    await page.locator('.o_field_widget[name=favicon] input[type=file]').setInputFiles({
      name: 'icon.png', mimeType: 'image/png', buffer: Buffer.from(PNG, 'base64'),
    });
    await page.locator('.o_form_button_save').click();
    await expect.poll(async () => (await callKw(page.request, TENANT, 'ncollection.product.branding', 'read',
      [[id], ['name']])).result[0].name).toBe('Acme Workspace');

    const login = await anonymousPage(page, '/web/login');
    expect(login.title).toBe('Acme Workspace');
    expect(login.text).toContain('Acme Workspace');
    expect(login.text).not.toMatch(/odoo/i);
    const second = login.icons.join(' ');
    expect(second).toContain('/ncollection/brand/favicon?v=');
    expect(second, 'a changed favicon has a new URL').not.toEqual(first);
    const reset = await anonymousPage(page, '/web/reset_password');
    expect(reset.title).toBe('Acme Workspace');
    expect(reset.text).not.toMatch(/odoo/i);

    const icon = await page.request.get(`${BASE}/ncollection/brand/favicon`);
    expect(icon.status()).toBe(200);
    expect(icon.headers()['content-type']).toBe('image/png');
    expect(icon.headers()['x-content-type-options']).toBe('nosniff');
    expect((await icon.body()).equals(Buffer.from(PNG, 'base64'))).toBe(true);
    expect(errors, 'no uncaught page error').toEqual([]);
  });

  test('a script, an SVG and a renamed file are refused; the company brand is untouched', async ({ page }) => {
    const id = await brandId(page);
    const company = (await callKw(page.request, TENANT, 'res.company', 'search_read', [[]], { fields: ['logo'], limit: 1 })).result[0];
    for (const [label, data] of [['svg', SVG], ['text', Buffer.from('<script>alert(1)</script>').toString('base64')]] as const) {
      const res = await callKw(page.request, TENANT, 'ncollection.product.branding', 'write', [[id], { logo: data }]);
      expect(res.error, `${label} refused`).toBeTruthy();
    }
    const after = (await callKw(page.request, TENANT, 'res.company', 'search_read', [[]], { fields: ['logo'], limit: 1 })).result[0];
    expect(after.logo).toEqual(company.logo);
  });

  test('the error page, the backend tab and the user menu carry the product name and no vendor name', async ({ page }) => {
    const errors = collectErrors(page);
    const id = await brandId(page);
    await callKw(page.request, TENANT, 'ncollection.product.branding', 'write', [[id], { name: 'Acme Workspace' }]);
    const missing = await anonymousPage(page, '/this-page-does-not-exist');
    expect(missing.status).toBe(404);
    expect(missing.title).toBe('Acme Workspace');
    expect(missing.text).not.toMatch(/odoo/i);
    expect(missing.icons.join(' ')).not.toContain('/ncollection_branding/static');
    await page.goto(`${BASE}/odoo`, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.o_main_navbar', { timeout: 90_000 });
    await expect.poll(() => page.title(), { timeout: 60_000 }).not.toMatch(/odoo/i);
    await page.locator('.o_user_menu button, .o_user_menu .dropdown-toggle').first().click();
    const menu = await page.locator('.o-dropdown--menu').innerText();
    expect(menu).not.toMatch(/odoo/i);
    expect(errors, 'no uncaught page error').toEqual([]);
  });

  test('Settings shows no link to the vendor documentation and no vendor name', async ({ page }) => {
    await page.goto(`${BASE}/odoo/settings`, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.o_setting_box, .app_settings_block, .o_form_view', { timeout: 120_000 });
    await page.waitForFunction(() => document.body.innerText.includes('API Keys'), undefined, { timeout: 120_000 });
    expect(await page.locator('a[href*="odoo.com"]').count()).toBe(0);
    expect(await page.locator('body').innerText()).not.toMatch(/odoo/i);
  });
});

test.describe('Company stamp from Accounting', () => {
  test('the entry is under Accounting, lists accounting stamps, and an ordinary user cannot read the stamp image', async ({ page }) => {
    await loginViaRpc(page, TENANT);
    const types = (await callKw(page.request, TENANT, 'ncollection.document.type', 'search_read',
      [[['code', '=', 'customer_invoice']]], { fields: ['id'] })).result;
    const stamp = (await callKw(page.request, TENANT, 'ncollection.document.stamp', 'create', [{
      name: `E2E accounting stamp ${Date.now()}`, image: PNG, document_type_ids: [[6, 0, [types[0].id]]],
    }])).result;
    const login = `e2e_plain_${Date.now()}`;
    const secret = `Pw-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const group = (await callKw(page.request, TENANT, 'ir.model.data', 'search_read',
      [[['module', '=', 'base'], ['name', '=', 'group_user']]], { fields: ['res_id'] })).result[0].res_id;
    const user = (await callKw(page.request, TENANT, 'res.users', 'create', [{
      name: 'E2E Plain', login, password: secret, group_ids: [[6, 0, [group]]],
    }])).result;
    try {
      // Accounting > Configuration > Document Stamps
      await page.goto(`${BASE}/odoo/action-ncollection_account_core.action_accounting_document_stamp`, { waitUntil: 'domcontentloaded' });
      await page.waitForSelector('.o_list_view', { timeout: 120_000 });
      await expect(page.locator('.o_list_view')).toContainText('E2E accounting stamp');
      const menus = (await callKw(page.request, TENANT, 'ir.ui.menu', 'search_read',
        [[['name', '=', 'Document Stamps']]], { fields: ['complete_name'] })).result.map((m: { complete_name: string }) => m.complete_name);
      expect(menus.some((n: string) => n.includes('Configuration'))).toBe(true);

      // the same stamp image, over the web: the owner gets it, an ordinary user does not
      const url = `${BASE}/web/image/ncollection.document.stamp/${stamp}/image`;
      const owner = await page.request.get(url);
      expect(owner.status()).toBe(200);
      expect((await owner.body()).equals(Buffer.from(PNG, 'base64'))).toBe(true);
      const other = await page.context().browser()!.newContext();
      const otherPage = await other.newPage();
      await loginViaRpc(otherPage, TENANT, login, secret);
      const refused = await otherPage.request.get(url);
      expect((await refused.body()).equals(Buffer.from(PNG, 'base64')), 'the raw stamp is not served').toBe(false);
      const rpc = await callKw(otherPage.request, TENANT, 'ncollection.document.stamp', 'read', [[stamp], ['image']]);
      expect(rpc.error, 'the image field is refused over RPC').toBeTruthy();
      await other.close();
    } finally {
      await callKw(page.request, TENANT, 'ncollection.document.stamp', 'unlink', [[stamp]]);
      await callKw(page.request, TENANT, 'res.users', 'write', [[user], { active: false }]);
    }
  });
});
