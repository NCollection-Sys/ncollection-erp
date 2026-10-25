import { test, expect, Page } from '@playwright/test';
import { execFileSync } from 'child_process';
import * as path from 'path';
import { activateLanguage, callKw, loginViaRpc, NAVBAR, TENANTS, TenantKey } from '../fixtures/tenants';

/**
 * One visual language for a partner's business role, and human page titles.
 *  - Customer / Vendor badges: a DIFFERENT colour per role (the NCollection role tokens), the word always on the badge,
 *    the same component on the invoice form and in the partner dropdown, a partner that is both shows both, and the
 *    same holds in Arabic (RTL).
 *  - The SaaS Summary (a form on a fresh record) is titled "Summary" / "الملخّص" in its breadcrumb - never
 *    `ncollection.subscription.dashboard,NewId_0x...` - also after a reload and after navigating away and back.
 * Synthetic partners only; the admin's language is restored after every test.
 */
const TENANT = 'e2eclienta' as const;
const PLATFORM = 'e2eadmin' as const;
const STAMP = String(Date.now()).slice(-6);

async function rpc(page: Page, tenant: TenantKey, model: string, method: string, args: unknown[],
  kwargs: Record<string, unknown> = {}) {
  const res = await callKw(page.request, tenant, model, method, args, kwargs);
  expect(res.error, JSON.stringify(res.error)).toBeUndefined();
  return res.result;
}

async function setLang(page: Page, tenant: TenantKey, lang: string) {
  await activateLanguage(page.request, tenant, lang);
  await rpc(page, tenant, 'res.users', 'write', [[2], { lang }]);
}

/** The colours a badge is drawn with, as the browser resolved them. */
async function colours(page: Page, selector: string) {
  return page.locator(selector).first().evaluate((el) => {
    const s = getComputedStyle(el);
    return { color: s.color, background: s.backgroundColor };
  });
}

test.describe('Customer / Vendor badges', () => {
  test.afterEach(async ({ page }) => {
    await setLang(page, TENANT, 'en_US');
  });

  for (const lang of ['en_US', 'ar_001']) {
    test(`two roles, two colours, words kept, form and dropdown alike (${lang})`, async ({ page }) => {
      await loginViaRpc(page, TENANT);
      await setLang(page, TENANT, lang);
      const name = `Roles E2E ${STAMP} ${lang}`;
      await rpc(page, TENANT, 'res.partner', 'create', [{ name: `${name} Both`, customer_rank: 1, supplier_rank: 1 }]);
      await rpc(page, TENANT, 'res.partner', 'create', [{ name: `${name} Vendor`, supplier_rank: 1 }]);

      await page.goto(`${TENANTS[TENANT]}/odoo/action-account.action_move_out_invoice_type/new`,
        { waitUntil: 'domcontentloaded' });
      await page.waitForSelector(NAVBAR, { timeout: 90_000 });
      // Odoo's own reading direction (the backend has no <html dir>; mirrored rules hang on body.o_rtl)
      expect(await page.evaluate(() => (window as any).odoo.loader.modules.get('@web/core/l10n/localization')?.localization?.direction)).toBe(lang === 'ar_001' ? 'rtl' : 'ltr');
      expect(await page.evaluate(() => document.body.classList.contains('o_rtl'))).toBe(lang === 'ar_001');

      // the dropdown: each role badge carries its role class (Odoo's odoomark draws them all alike otherwise)
      const input = page.locator('.o_field_widget[name=partner_id] input').first();
      await input.fill(`${name} Both`);
      const row = page.locator('.o-autocomplete--dropdown-item', { hasText: `${name} Both` }).first();
      await expect(row.locator('.o_nc_role_customer')).toHaveCount(1);
      await expect(row.locator('.o_nc_role_vendor')).toHaveCount(1);
      const dropCustomer = await colours(page, '.o-autocomplete--dropdown-item .o_nc_role_customer');
      const dropVendor = await colours(page, '.o-autocomplete--dropdown-item .o_nc_role_vendor');
      expect(dropCustomer.background).not.toEqual(dropVendor.background);
      expect(dropCustomer.color).not.toEqual(dropVendor.color);
      await row.click();

      // the form: the same component, both roles, the words stay
      const customer = page.locator('.o_nc_partner_roles .o_nc_role_customer');
      const vendor = page.locator('.o_nc_partner_roles .o_nc_role_vendor');
      await expect(customer).toBeVisible();
      await expect(vendor).toBeVisible();
      await expect(customer).toHaveText(lang === 'ar_001' ? 'عميل' : 'Customer');
      await expect(vendor).toHaveText(lang === 'ar_001' ? 'مورد' : 'Vendor');
      const formCustomer = await colours(page, '.o_nc_partner_roles .o_nc_role_customer');
      const formVendor = await colours(page, '.o_nc_partner_roles .o_nc_role_vendor');
      expect(formCustomer.background, 'customer and vendor are told apart by colour').not.toEqual(formVendor.background);
      expect(formCustomer, 'the dropdown and the form use one component').toEqual(dropCustomer);
      expect(formVendor).toEqual(dropVendor);
      // the colours are the NCollection role tokens, not Bootstrap's
      const tokens = await page.evaluate(() => {
        const root = getComputedStyle(document.documentElement);
        return ['--nc-color-customer', '--nc-color-vendor'].map((t) => root.getPropertyValue(t).trim());
      });
      expect(tokens.every(Boolean), 'the role tokens are defined').toBe(true);
    });
  }
});

/** Every URL the main frame committed (loads, redirects' final targets, pushState / replaceState). */
function committedUrls(page: Page): string[] {
  const urls: string[] = [];
  page.on('framenavigated', (frame) => { if (frame === page.mainFrame()) urls.push(frame.url()); });
  return urls;
}

function noOdooInTheAddressBar(urls: string[]) {
  const app = urls.filter((u) => !/\/web\/|\/report\/|\/ncollection\//.test(new URL(u).pathname + '/'));
  expect(app.filter((u) => new URL(u).pathname.startsWith('/odoo')), 'no /odoo in the address bar').toEqual([]);
}

test.describe('Application URLs (/atlas, never /odoo)', () => {
  test('login, menus, a record, create + save, reload, back / forward, a legacy /odoo link', async ({ page }) => {
    const urls = committedUrls(page);
    const base = TENANTS[TENANT];
    await page.goto(`${base}/web/login`, { waitUntil: 'domcontentloaded' });
    await page.locator('input[name=login]').fill('admin');
    await page.locator('input[name=password]').fill('admin');
    await page.locator('form.oe_login_form button[type=submit]').click();
    await page.waitForSelector(NAVBAR, { timeout: 90_000 });
    expect(new URL(page.url()).pathname, 'logging in lands on the application path').toMatch(/^\/atlas(\/|$)/);

    // a list, then a record from it, then back and forward
    await page.goto(`${base}/atlas/action-account.action_move_out_invoice_type`, { waitUntil: 'domcontentloaded' });
    await expect(page.locator('.o_list_view').first()).toBeVisible({ timeout: 60_000 });
    const list = page.url();
    expect(new URL(list).pathname).toMatch(/^\/atlas\/[a-z]/);
    await page.locator('.o_list_button_add, .o-kanban-button-new').first().click();
    await expect(page).toHaveURL(/\/atlas\/[^/]+\/new$/);
    await page.locator('.o_field_widget[name=partner_id] input').first().fill('Azure');
    await page.locator('.o-autocomplete--dropdown-item').first().click();
    await page.locator('.o_form_button_save').click();
    await expect(page).toHaveURL(/\/atlas\/[^/]+\/\d+$/, { timeout: 30_000 });
    const record = page.url();
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.locator('.o_form_view')).toBeVisible({ timeout: 60_000 });
    expect(page.url(), 'a deep /atlas link survives a hard refresh').toBe(record);
    // back / forward: every history entry is an /atlas URL (after a save Odoo keeps /new and the record as two
    // entries, exactly as it does under /odoo), and forward returns to the record
    await page.goBack();
    await expect(page).toHaveURL(/\/atlas\/[^/]+\/new$/, { timeout: 60_000 });
    await page.goBack();
    await expect(page.locator('.o_list_view').first()).toBeVisible({ timeout: 60_000 });
    expect(new URL(page.url()).pathname, 'back on the list, under /atlas').toMatch(/^\/atlas\/[a-z]/);
    await page.goForward();
    await page.goForward();
    await expect(page).toHaveURL(record, { timeout: 60_000 });

    // a link Odoo itself generated (e-mail, chatter) still works and lands on /atlas
    await page.goto(`${base}/odoo/action-account.action_move_out_invoice_type`, { waitUntil: 'domcontentloaded' });
    await expect(page.locator('.o_list_view').first()).toBeVisible({ timeout: 60_000 });
    expect(new URL(page.url()).pathname).toMatch(/^\/atlas\/[a-z]/);
    noOdooInTheAddressBar(urls);
    expect(urls.some((u) => new URL(u).pathname.startsWith('/atlas/')), 'the /atlas namespace was used').toBe(true);
  });
});

test.describe('Company Entry and the Release section', () => {
  const stale = 'e2eclientb';
  const cwd = path.resolve(__dirname, '..', '..');
  const sql = (db: string, q: string) => execFileSync('docker', ['compose', 'exec', '-T', 'db', 'psql', '-U', 'odoo',
    '-d', db, '-Atc', q], { cwd }).toString().trim();
  let core = '';

  test.beforeAll(() => {
    core = sql(stale, "SELECT latest_version FROM ir_module_module WHERE name = 'ncollection_core'");
    // What provisioning gives every tenant: the platform's derived config-sync key, so the platform can read the
    // tenant's release state (its gate answers 404 to anybody else) and register an entry grant.
    const code = [
      'import base64, hashlib, hmac, os',
      'key = base64.urlsafe_b64encode(hmac.new(os.environ["NC_CONFIG_SYNC_KEY"].encode(),',
      '    b"nc-config-sync:" + env.cr.dbname.encode(), hashlib.sha256).digest()).decode()',
      'env["ncollection.config.sync.key"]._install_key(key, create_user=True)',
      'env.cr.commit()',
    ].join('\n');
    for (const db of ['e2eclienta', stale]) {
      execFileSync('docker', ['compose', 'exec', '-T', 'odoo', 'odoo', 'shell', '-d', db, '--no-http',
        '--log-level=error', '--db_host=db', '--db_user=odoo', '--db_password=odoo'], { input: code, cwd });
    }
  });
  test.afterAll(() => {
    if (core) sql(stale, `UPDATE ir_module_module SET latest_version = '${core}' WHERE name = 'ncollection_core'`);
  });

  test('a stale tenant refuses entry with the exact reason; a READY one is entered on /atlas', async ({ page }) => {
    test.setTimeout(420_000);
    const urls = committedUrls(page);
    await loginViaRpc(page, PLATFORM);
    const tenants = await rpc(page, PLATFORM, 'ncollection.tenant', 'search_read',
      [[['database_name', 'in', ['e2eclienta', stale]]]], { fields: ['id', 'database_name'] });
    const id = Object.fromEntries(tenants.map((t: { id: number; database_name: string }) => [t.database_name, t.id]));
    await rpc(page, PLATFORM, 'ncollection.tenant', 'write', [Object.values(id), {
      status: 'active', platform_admin_ids: [[4, 2]] }]);
    sql(stale, "UPDATE ir_module_module SET latest_version = '19.0.1.1.0' WHERE name = 'ncollection_core'");

    // "Enter Company" lives in the Summary's "My Companies" list (the platform's entry point)
    const summary = `${TENANTS[PLATFORM]}/atlas/action-ncollection_subscription.action_ncollection_subscription_dashboard`;
    const enter = async (company: string) => {
      await page.goto(summary, { waitUntil: 'domcontentloaded' });
      const row = page.locator('.o_ncollection_companies .o_data_row', { hasText: company }).first();
      await row.locator('button[name=action_enter_company]').click({ timeout: 90_000 });
    };
    const names = await rpc(page, PLATFORM, 'ncollection.tenant', 'read', [[id[stale], id.e2eclienta], ['company_name']]);
    const name = Object.fromEntries(names.map((t: { id: number; company_name: string }) => [t.id, t.company_name]));

    // the stale tenant: refused, nothing issued, the Release section says exactly why and what to do
    await enter(name[id[stale]]);
    await expect(page.locator('.o_notification')).toContainText('cannot be entered right now', { timeout: 30_000 });
    expect(new URL(page.url()).host, 'no navigation into the stale tenant').toBe(new URL(TENANTS[PLATFORM]).host);
    await page.goto(`${TENANTS[PLATFORM]}/atlas/action-ncollection_subscription.action_ncollection_tenant/${id[stale]}`,
      { waitUntil: 'domcontentloaded' });
    await expect(page.locator('.o_nc_release_banner')).toContainText('Upgrade required', { timeout: 120_000 });
    await expect(page.locator('.o_nc_release_banner')).toContainText('ncollection_core');
    await page.locator('.o_notebook a', { hasText: 'System' }).click();
    await expect(page.locator('.o_field_widget[name=nc_release_required]')).toContainText('ncollection_core');
    await expect(page.locator('.o_field_widget[name=nc_release_detail]')).toContainText('ncollection_core: database 19.0.1.1.0 < code');
    await expect(page.locator('.o_field_widget[name=nc_release_code_fingerprint]')).not.toBeEmpty();

    // the READY tenant next to it is entered normally, into its /atlas application path
    await enter(name[id.e2eclienta]);
    // the tenant's public address on the configured base domain (e2eclienta.<base domain>), into /atlas
    await page.waitForURL((u) => u.hostname.startsWith('e2eclienta.') && u.pathname.startsWith('/atlas'),
      { timeout: 90_000 });
    await page.waitForSelector(NAVBAR, { timeout: 90_000 });
    noOdooInTheAddressBar(urls);
  });
});

test.describe('Settings: no Enterprise upsell', () => {
  test('no app section shows an Enterprise option, badge or upgrade dialog', async ({ page }) => {
    test.setTimeout(300_000);
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(String(e)));
    await loginViaRpc(page, TENANT);
    await page.goto(`${TENANTS[TENANT]}/atlas/settings`, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.o_settings_container, .settings', { timeout: 120_000 });
    const apps = await page.locator('.settings_tab .tab, .o_setting_tab').allInnerTexts();
    expect(apps.length, 'several apps have settings here').toBeGreaterThan(2);
    for (const app of apps.slice(0, 8)) {
      await page.locator('.settings_tab .tab, .o_setting_tab', { hasText: app }).first().click();
      await page.waitForTimeout(500);
      await expect(page.locator('.o_field_upgrade_boolean:visible'), `${app}: an upsell option`).toHaveCount(0);
      await expect(page.locator('.o_settings_container :text-is("Enterprise"):visible'), `${app}: an Enterprise badge`)
        .toHaveCount(0);
    }
    await expect(page.locator('.modal', { hasText: 'Odoo Enterprise' })).toHaveCount(0);
    // the safety net: even a rendered upsell widget never opens the dialog (the field is in the view, hidden)
    const reached = await page.evaluate(() => document.querySelectorAll('.o_field_upgrade_boolean input').length);
    if (reached) {
      await page.evaluate(() => (document.querySelector('.o_field_upgrade_boolean input') as HTMLInputElement).click());
      await page.waitForTimeout(800);
      await expect(page.locator('.modal', { hasText: 'Enterprise' })).toHaveCount(0);
    }
    expect(errors).toEqual([]);
  });
});

test.describe('Vendor bills take products', () => {
  test('a manual vendor bill shows Product, takes one and posts', async ({ page }) => {
    test.setTimeout(300_000);
    await loginViaRpc(page, TENANT);
    await page.goto(`${TENANTS[TENANT]}/atlas/action-account.action_move_in_invoice_type/new`, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.o_form_view', { timeout: 120_000 });
    expect(new URL(page.url()).pathname).toMatch(/^\/atlas\//);
    const lines = page.locator('.o_field_widget[name=invoice_line_ids]');
    await expect(lines.locator('th[data-name=product_id]'), 'the Product column is shown on a bill').toBeVisible();
    await page.locator('.o_field_widget[name=partner_id] input').first().fill('Azure');
    await page.locator('.o-autocomplete--dropdown-item').first().click();
    await page.locator('.o_field_widget[name=invoice_date] input').fill('01/15/2026');
    await lines.locator('.o_field_x2many_list_row_add a').first().click();
    await page.locator('.o_selected_row .o_field_widget[name=product_id] input').fill('a');
    await page.locator('.o-autocomplete--dropdown-item').first().click();
    await page.locator('.o_selected_row .o_field_widget[name=quantity] input').fill('2');
    await page.locator('.o_form_button_save').click();
    await expect(page).toHaveURL(/\/atlas\/[^/]+\/\d+$/, { timeout: 60_000 });
    await expect(lines.locator('.o_data_row td[name=product_id]').first()).not.toBeEmpty();
    await page.locator('button[name=action_post]').first().click();
    await expect(page.locator('.o_statusbar_status .o_arrow_button_current')).toContainText(/Posted/, { timeout: 60_000 });
  });
});

test.describe('Page titles', () => {
  test.afterEach(async ({ page }) => {
    await setLang(page, PLATFORM, 'en_US');
  });

  for (const [lang, title] of [['en_US', 'Summary'], ['ar_001', 'الملخّص']] as const) {
    test(`the SaaS Summary breadcrumb is its name, not a technical id (${lang})`, async ({ page }) => {
      test.setTimeout(420_000);
      await loginViaRpc(page, PLATFORM);
      await setLang(page, PLATFORM, lang);
      const url = `${TENANTS[PLATFORM]}/odoo/action-ncollection_subscription.action_ncollection_subscription_dashboard`;
      const crumb = page.locator('.o_breadcrumb');
      const check = async (where: string) => {
        await expect(crumb, where).toContainText(title, { timeout: 150_000 });
        await expect(crumb, where).not.toContainText(/NewId|ncollection\.|subscription\.dashboard/);
      };
      await page.goto(url, { waitUntil: 'domcontentloaded' });
      await check('first open');
      await page.reload({ waitUntil: 'domcontentloaded' });
      await check('after a reload');
      // away to the Tenants list through the app's menu, then back and forward with the browser
      await page.locator('.o_menu_sections a, .o_menu_sections button', { hasText: lang === 'ar_001' ? 'المستأجرون' : 'Tenants' })
        .first().click();
      await expect(crumb).toContainText(lang === 'ar_001' ? 'المستأجرون' : 'Tenants', { timeout: 60_000 });
      expect(new URL(page.url()).pathname).toMatch(/^\/atlas\//);
      await page.goBack();
      await check('after back');
      await page.goForward();
      await expect(crumb).toContainText(lang === 'ar_001' ? 'المستأجرون' : 'Tenants', { timeout: 60_000 });
      await page.goBack();
      await check('after back again');
      // Odoo's own reading direction (the backend has no <html dir>; mirrored rules hang on body.o_rtl)
      expect(await page.evaluate(() => (window as any).odoo.loader.modules.get('@web/core/l10n/localization')?.localization?.direction)).toBe(lang === 'ar_001' ? 'rtl' : 'ltr');
      expect(await page.evaluate(() => document.body.classList.contains('o_rtl'))).toBe(lang === 'ar_001');
    });
  }
});
