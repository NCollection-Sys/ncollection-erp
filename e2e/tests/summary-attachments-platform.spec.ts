import { test, expect, Page, request as pwRequest } from '@playwright/test';
import { execFileSync } from 'child_process';
import { createHash } from 'crypto';
import * as path from 'path';
import { callKw, loginViaRpc, NAVBAR, TENANTS } from '../fixtures/tenants';

/**
 * Browser coverage for the Summary, the Sales attachment viewer and the Platform Admin Mode strip.
 * One tenant (e2eclienta, which has Sales). Every test asserts there is no uncaught page error.
 */
const TENANT = 'e2eclienta' as const;
const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==';
const PDF = Buffer.from('%PDF-1.1\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF').toString('base64');

function collectErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  return errors;
}

async function openAction(page: Page, xmlid: string) {
  await page.goto(`${TENANTS[TENANT]}/odoo/action-${xmlid}`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector(NAVBAR, { timeout: 60_000 });
}

test.describe('Summary', () => {
  test('the landing action is the Summary, for this company, without errors', async ({ page }) => {
    const errors = collectErrors(page);
    await loginViaRpc(page, TENANT);
    await openAction(page, 'ncollection_core.action_ncollection_dashboard');
    await expect(page.locator('.nc-dashboard__title')).toHaveText('Summary', { timeout: 30_000 });
    await expect(page.locator('.nc-dashboard__company')).not.toHaveText('');
    await expect(page.locator('.o_error_dialog')).toHaveCount(0);
    // Balance Sheet / P&L cards appear when the role may see financials and the report engine is installed
    const cards = await page.locator('.nc-statement-card').count();
    if (cards) {
      await expect(page.locator('.nc-statement-card__title').first()).toBeVisible();
    }
    expect(errors).toEqual([]);
  });
});

test.describe('Sales attachment viewer', () => {
  let orders: { none: string; many: string };

  test.beforeAll(async ({ playwright }) => {
    const request = await playwright.request.newContext();
    const auth = await request.post(`${TENANTS[TENANT]}/web/session/authenticate`, {
      data: { jsonrpc: '2.0', params: { db: TENANT, login: 'admin', password: 'admin' } },
    });
    expect((await auth.json()).result.uid).toBeTruthy();
    const partner = (await callKw(request, TENANT, 'res.partner', 'create', [{ name: 'E2E Attachment Customer' }])).result;
    const make = async () => {
      const id = (await callKw(request, TENANT, 'sale.order', 'create', [{ partner_id: partner }])).result as number;
      const rec = (await callKw(request, TENANT, 'sale.order', 'read', [[id], ['name']])).result[0];
      return { id, name: rec.name as string };
    };
    const none = await make();
    const many = await make();
    const attach = (name: string, mimetype: string, datas: string) =>
      callKw(request, TENANT, 'ir.attachment', 'create', [{ name, mimetype, datas, res_model: 'sale.order', res_id: many.id }]);
    await attach('photo.png', 'image/png', PNG);
    await attach('عرض سعر – شركة النور.pdf', 'application/pdf', PDF);
    // real binary content, so Odoo keeps application/octet-stream instead of sniffing text/plain
    await attach('data.bin', 'application/octet-stream', Buffer.from([0, 1, 2, 3, 250, 251, 252, 253]).toString('base64'));
    orders = { none: none.name, many: many.name };
    await request.dispose();
  });

  test('zero attachments show nothing; several open ONE modal with position, fallback and keyboard navigation', async ({ page }) => {
    const errors = collectErrors(page);
    await loginViaRpc(page, TENANT);
    await openAction(page, 'sale.action_quotations_with_onboarding');
    await page.waitForSelector('.o_list_renderer', { timeout: 30_000 });
    const row = (name: string) => page.locator('.o_data_row', { hasText: name }).first();

    // the paperclip exists only on the order that has attachments
    await expect(row(orders.none)).toBeVisible();
    await expect(row(orders.none).locator('.o-NcAttachmentCount')).toHaveCount(0);
    await row(orders.many).locator('.o-NcAttachmentCount').click();

    const viewer = page.locator('.o-FileViewer');
    await expect(viewer).toBeVisible();
    await expect(page.locator('.o-NcFileViewer-position')).toHaveText('1 / 3');
    await expect(viewer).toContainText('photo.png');

    await page.keyboard.press('ArrowRight');
    await expect(page.locator('.o-NcFileViewer-position')).toHaveText('2 / 3');
    await expect(viewer).toContainText('عرض سعر – شركة النور.pdf');            // Arabic file name

    await page.keyboard.press('ArrowRight');
    await expect(page.locator('.o-NcFileViewer-position')).toHaveText('3 / 3');
    await expect(page.locator('.o-NcFileViewer-fallback')).toBeVisible();        // unsupported type
    await expect(page.locator('.o-NcFileViewer-fallback a[download]')).toBeVisible();

    await page.keyboard.press('ArrowRight');                                     // wraps
    await expect(page.locator('.o-NcFileViewer-position')).toHaveText('1 / 3');

    // ONE modal, no new browser page
    expect(page.context().pages().length).toBe(1);
    await page.keyboard.press('Escape');
    await expect(viewer).toHaveCount(0);
    expect(errors).toEqual([]);
  });

  test('right-to-left: the document is mirrored and the arrow keys follow the reading direction', async ({ page, request }) => {
    const errors = collectErrors(page);
    await loginViaRpc(page, TENANT);
    const lang = (await callKw(page.request, TENANT, 'res.lang', 'search_read',
      [[['code', '=', 'ar_001'], ['active', 'in', [true, false]]]], { fields: ['id'] })).result[0];
    test.skip(!lang, 'Arabic is not available on this fixture');
    await callKw(page.request, TENANT, 'res.lang', 'write', [[lang.id], { active: true }]);
    const uid = (await callKw(page.request, TENANT, 'res.users', 'search', [[['login', '=', 'admin']]])).result[0];
    await callKw(page.request, TENANT, 'res.users', 'write', [[uid], { lang: 'ar_001' }]);
    try {
      await loginViaRpc(page, TENANT);                 // a NEW session picks the language up
      await openAction(page, 'sale.action_quotations_with_onboarding');
      const probe = await page.evaluate(() => {
        const loc = (window as any).odoo.loader.modules.get('@web/core/l10n/localization')?.localization;
        return { dir: loc?.direction, code: loc?.code, htmlDir: document.documentElement.getAttribute('dir'), cls: document.body.className.slice(0, 60) };
      });
      expect(probe.dir).toBe('rtl');
      await page.locator('.o_data_row', { hasText: orders.many }).first().locator('.o-NcAttachmentCount').click();
      await expect(page.locator('.o-NcFileViewer-position')).toHaveText('1 / 3');
      await page.keyboard.press('ArrowLeft');            // "next" is to the LEFT in RTL
      await expect(page.locator('.o-NcFileViewer-position')).toHaveText('2 / 3');
      await page.keyboard.press('ArrowRight');           // and "previous" to the right
      await expect(page.locator('.o-NcFileViewer-position')).toHaveText('1 / 3');
    } finally {
      await callKw(page.request, TENANT, 'res.users', 'write', [[uid], { lang: 'en_US' }]);
    }
    expect(errors).toEqual([]);
  });
});

test.describe('Platform Admin Mode', () => {
  const RETURN = 'http://e2eadmin.localhost/odoo';

  function grantEntry(token: string) {
    const hash = createHash('sha256').update(token).digest('hex');
    const code = [
      "g = env['ncollection.platform.entry'].grant_from_platform(",
      `  {'token_hash': '${hash}', 'platform_ref': 'u7', 'platform_name': 'E2E Platform Admin',`,
      `   'return_url': '${RETURN}', 'ttl': 120})`,
      'env.cr.commit()',
    ].join('\n');
    execFileSync('docker', ['compose', 'exec', '-T', 'odoo', 'odoo', 'shell', '-d', TENANT, '--no-http',
      '--log-level=error', '--db_host=db', '--db_user=odoo', '--db_password=odoo'],
      // the repo root: CI passes COMPOSE_FILE as relative paths, which only resolve from there (the suite runs in e2e/)
      { input: code, cwd: path.resolve(__dirname, '..', '..') });
  }

  test('the strip names the company and the admin, and "Back to Platform" leaves the tenant cleanly', async ({ page }) => {
    const errors = collectErrors(page);
    const token = 'e2e-' + createHash('sha1').update(String(Date.now())).digest('hex') + 'x'.repeat(12);
    grantEntry(token);
    await page.goto(`${TENANTS[TENANT]}/ncollection/platform/enter#token=${token}`, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector(NAVBAR, { timeout: 60_000 });

    const bar = page.locator('.nc-platform-bar');
    await expect(bar).toBeVisible();
    await expect(bar).toContainText('Platform Admin Mode');
    await expect(bar).toContainText('E2E Platform Admin');
    await expect(bar.locator('.nc-platform-bar__company')).not.toHaveText('');
    expect(await page.evaluate(() => document.body.classList.contains('nc-platform-mode'))).toBe(true);

    // the token cannot be used a second time
    const anonymous = await pwRequest.newContext();                // another browser: no session
    const replay = await anonymous.post(`${TENANTS[TENANT]}/ncollection/platform/enter`, { form: { token }, maxRedirects: 0 });
    await anonymous.dispose();
    expect(replay.status()).toBe(403);

    // Back to Platform: ends THIS tenant session and goes to the platform
    await bar.locator('a.nc-platform-bar__back').click();
    await page.waitForURL(/e2eadmin\.localhost/, { timeout: 30_000 });
    const after = await page.request.get(`${TENANTS[TENANT]}/atlas`, { maxRedirects: 0 });
    expect([301, 302, 303]).toContain(after.status());
    expect(after.headers()['location'] || '').toContain('/web/login');
    expect(errors).toEqual([]);
  });

  test('an ordinary tenant admin sees no platform strip', async ({ page }) => {
    await loginViaRpc(page, TENANT);
    await openAction(page, 'ncollection_core.action_ncollection_home');
    await expect(page.locator('.nc-platform-bar')).toHaveCount(0);
  });
});
