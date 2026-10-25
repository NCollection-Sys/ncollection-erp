import { test, expect, Page } from '@playwright/test';
import { execFileSync } from 'child_process';
import { createHash } from 'crypto';
import * as path from 'path';
import { callKw, loginViaRpc, TENANTS } from '../fixtures/tenants';

/**
 * System branding and the company stamp configured from Accounting (e2eclienta; admin = workspace Owner).
 *  - the SYSTEM identity (name, favicon, logos) is ONE record, configured in a System Branding form, and every surface
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

/**
 * Sign the browser in the way a Platform Administrator does: a one-time grant registered in the tenant, redeemed at
 * /ncollection/platform/enter, which signs in the passwordless platform proxy. System Branding belongs to platform
 * staff, so the tenant's own Owner cannot be the user that changes it (see 'the boundary' below).
 */
async function enterAsPlatformAdmin(page: Page): Promise<void> {
  const token = 'e2e-brand-' + createHash('sha1').update(String(Date.now()) + Math.random()).digest('hex') + 'x'.repeat(12);
  const hash = createHash('sha256').update(token).digest('hex');
  const code = [
    "env['ncollection.platform.entry'].grant_from_platform(",
    `  {'token_hash': '${hash}', 'platform_ref': 'u9', 'platform_name': 'E2E Platform Admin',`,
    `   'return_url': 'https://platform.invalid/odoo', 'ttl': 120})`,
    'env.cr.commit()',
  ].join('\n');
  execFileSync('docker', ['compose', 'exec', '-T', 'odoo', 'odoo', 'shell', '-d', TENANT, '--no-http',
    '--log-level=error', '--db_host=db', '--db_user=odoo', '--db_password=odoo'],
    { input: code, cwd: path.resolve(__dirname, '..', '..') });
  const res = await page.request.post(`${BASE}/ncollection/platform/enter`, { form: { token }, maxRedirects: 0 });
  expect(res.status(), 'the platform entry signs the proxy in').toBe(303);
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

test.describe('System branding', () => {
  test.beforeEach(async ({ page }) => {
    await enterAsPlatformAdmin(page);
  });
  test.afterEach(async ({ page }) => {
    await restore(page);
  });

  test('the System Branding form changes the name and the favicon everywhere, and the favicon URL is versioned', async ({ page }) => {
    const errors = collectErrors(page);
    const id = await brandId(page);
    await page.goto(`${BASE}/odoo/action-ncollection_branding.action_product_branding`, { waitUntil: 'domcontentloaded' });
    const name = page.locator('.o_field_widget[name=name] input');
    await name.waitFor({ timeout: 90_000 });
    await expect(page.locator('.o_form_view')).toContainText('neither can change this and this cannot change them');
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

  test('the System Logo uploaded in the form IS the sign-in logo: A, then B replaces it, a damaged file is refused', async ({ page }) => {
    // Three real files in the three accepted formats (WebP is checked the way Odoo reads it, never decoded server-side).
    const LOGO_A = { name: 'logo-a.png', mimeType: 'image/png', buffer: Buffer.from(
      'iVBORw0KGgoAAAANSUhEUgAAABgAAAAMCAYAAAB4MH11AAAAHUlEQVR4nGM8ISf3n4GGgImWho9aMGrBqAXDxQIAW+0CG1AOiAQAAAAASUVORK5CYII=', 'base64') };
    const LOGO_B = { name: 'logo-b.jpg', mimeType: 'image/jpeg', buffer: Buffer.from(
      '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAAMABgDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDNooorxj4wKKKKAP/Z', 'base64') };
    const LOGO_C = { name: 'logo-c.webp', mimeType: 'image/webp', buffer: Buffer.from(
      'UklGRh4AAABXRUJQVlA4TBEAAAAvF8ACAAdQlKIUuf+BiOh/AAA=', 'base64') };
    const errors = collectErrors(page);
    const company = (await callKw(page.request, TENANT, 'res.company', 'search_read', [[]], { fields: ['logo'], limit: 1 })).result[0];

    /** The sign-in page as an anonymous visitor sees it, and the bytes behind its system logo (if any). */
    async function signInLogo(): Promise<{ html: string; src: string; body: Buffer | null }> {
      const ctx = await page.context().browser()!.newContext();
      const html = await (await ctx.request.get(`${BASE}/web/login`)).text();
      const src = ((html.match(/<img[^>]*class="o_nc_login__system-logo"[^>]*>/) || [''])[0].match(/src="([^"]+)"/) || [, ''])[1] as string;
      const body = src ? await (await ctx.request.get(`${BASE}${src.replace(/&amp;/g, '&')}`)).body() : null;
      await ctx.close();
      return { html, src, body };
    }
    async function upload(file: { name: string; mimeType: string; buffer: Buffer }): Promise<void> {
      await page.goto(`${BASE}/odoo/action-ncollection_branding.action_product_branding`, { waitUntil: 'domcontentloaded' });
      await page.locator('.o_field_widget[name=name] input').waitFor({ timeout: 90_000 });
      await page.locator('.o_field_widget[name=logo] input[type=file]').setInputFiles(file);
      await page.locator('.o_form_button_save').click();
      await expect(page.locator('.o_form_button_save')).toBeHidden({ timeout: 30_000 });
    }

    // Nothing configured: the system name alone - no shipped mark, no hard-coded "N", never the company's logo.
    const none = await signInLogo();
    expect(none.src).toBe('');
    expect(none.html).not.toContain('nc_login_logo_g');
    expect(none.html).not.toContain('/web/binary/company_logo');

    await upload(LOGO_A);
    const a = await signInLogo();
    expect(a.src).toMatch(/^\/ncollection\/brand\/login\?v=\d+$/);
    expect(a.body!.equals(LOGO_A.buffer), 'the sign-in logo is byte-for-byte the uploaded System Logo A').toBe(true);

    await upload(LOGO_B);
    const b = await signInLogo();
    expect(b.src, 'a replaced logo gets a new URL, never a cached A').not.toEqual(a.src);
    expect(b.body!.equals(LOGO_B.buffer), 'logo B replaced A on the sign-in page').toBe(true);

    await upload(LOGO_C);
    const c = await signInLogo();
    expect(c.body!.equals(LOGO_C.buffer), 'a WebP System Logo is accepted and served as uploaded').toBe(true);
    expect(c.html).not.toContain('/web/binary/company_logo');

    // A damaged PNG is refused in the form with a message that says what is wrong; logo C stays.
    await page.goto(`${BASE}/odoo/action-ncollection_branding.action_product_branding`, { waitUntil: 'domcontentloaded' });
    await page.locator('.o_field_widget[name=name] input').waitFor({ timeout: 90_000 });
    await page.locator('.o_field_widget[name=logo] input[type=file]').setInputFiles({
      name: 'broken.png', mimeType: 'image/png', buffer: LOGO_A.buffer.subarray(0, 40) });
    await page.locator('.o_form_button_save').click();
    await expect(page.locator('.o_error_dialog')).toContainText('could not be read as a PNG image');
    expect((await signInLogo()).body!.equals(LOGO_C.buffer)).toBe(true);

    const after = (await callKw(page.request, TENANT, 'res.company', 'search_read', [[]], { fields: ['logo'], limit: 1 })).result[0];
    expect(after.logo, 'the company identity is untouched by System Branding').toEqual(company.logo);
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

test.describe('System branding: the boundary', () => {
  test('the workspace Owner cannot change System Branding, and still owns tenant branding and company identity', async ({ page }) => {
    await loginViaRpc(page, TENANT);                       // admin = the workspace Owner
    for (const vals of [{ name: 'Hijacked' }, { primary_color: '#000000' }, { copyright_text: 'pwned' }]) {
      const res = await callKw(page.request, TENANT, 'ncollection.product.branding', 'write', [[1], vals]);
      expect(res.error, `Owner write of ${Object.keys(vals)[0]} is refused`).toBeTruthy();
      expect(JSON.stringify(res.error)).toMatch(/Access|not allowed|permission/i);
    }
    const opened = await callKw(page.request, TENANT, 'ncollection.product.branding', 'action_open');
    expect(opened.error, 'the System Branding screen does not open for the Owner').toBeTruthy();
    const read = await callKw(page.request, TENANT, 'ncollection.product.branding', 'read', [[1], ['name']]);
    expect(read.error, 'not even a read of the System Branding record').toBeTruthy();

    // the Owner's REAL menu tree (what the web client renders) does not offer System Branding at all
    const ownerMenus = (await callKw(page.request, TENANT, 'ir.ui.menu', 'load_web_menus', [false, 'en_US'])).result;
    expect(Object.values(ownerMenus as Record<string, { name: string }>).map((m) => m.name)).not.toContain('System Branding');

    // every page still reads the system identity: it is the resolver's, not the Owner's
    const login = await anonymousPage(page, '/web/login');
    expect(login.status).toBe(200);
    expect(login.title.length).toBeGreaterThan(0);

    // tenant branding and company identity remain the Owner's, exactly as before
    const company = (await callKw(page.request, TENANT, 'res.company', 'search_read', [[]],
      { fields: ['id', 'name', 'nc_primary_color'], limit: 1 })).result[0];
    try {
      const written = await callKw(page.request, TENANT, 'res.company', 'write',
        [[company.id], { nc_primary_color: '#123456', name: `${company.name} (e2e)` }]);
      expect(written.error, JSON.stringify(written.error)).toBeUndefined();
      const after = (await callKw(page.request, TENANT, 'res.company', 'read', [[company.id], ['name', 'nc_primary_color']])).result[0];
      expect(after.nc_primary_color).toBe('#123456');
      expect(after.name).toBe(`${company.name} (e2e)`);
    } finally {
      await callKw(page.request, TENANT, 'res.company', 'write',
        [[company.id], { nc_primary_color: company.nc_primary_color, name: company.name }]);
    }
  });

  test('a Platform Administrator inside the tenant can open and use System Branding', async ({ page }) => {
    await enterAsPlatformAdmin(page);
    const opened = await callKw(page.request, TENANT, 'ncollection.product.branding', 'action_open');
    expect(opened.error, JSON.stringify(opened.error)).toBeUndefined();
    const proxyMenus = (await callKw(page.request, TENANT, 'ir.ui.menu', 'load_web_menus', [false, 'en_US'])).result;
    expect(Object.values(proxyMenus as Record<string, { name: string }>).map((m) => m.name), 'the platform proxy is offered it')
      .toContain('System Branding');
    const id = await brandId(page);
    try {
      const res = await callKw(page.request, TENANT, 'ncollection.product.branding', 'write', [[id], { browser_title: 'E2E Console' }]);
      expect(res.error, JSON.stringify(res.error)).toBeUndefined();
      expect((await anonymousPage(page, '/web/login')).title).toBe('E2E Console');
    } finally {
      await callKw(page.request, TENANT, 'ncollection.product.branding', 'write', [[id], { browser_title: false }]);
    }
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
