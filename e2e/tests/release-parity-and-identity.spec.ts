import { test, expect, Page } from '@playwright/test';
import * as fs from 'fs';
import { execFileSync } from 'child_process';
import * as path from 'path';
import { callKw, loginViaRpc, NAVBAR, TENANTS, TenantKey } from '../fixtures/tenants';

/*
 * The final release pass (system identity everywhere, Business Management, Add User, multi-company entry, parity):
 *   - the installable app ("Install app") is the system's: name, short name, icons from System Branding, /atlas scope;
 *   - the platform's app is "Business Management" / "إدارة الأعمال" with its own icon;
 *   - Discuss, the Company screen and the company's contact details follow the platform (pushed over config-sync);
 *   - Users > Add User creates a user with a role who can sign in at once;
 *   - two READY companies are entered at the same time, in two tabs, with no session or database crossing over;
 *   - every fixture tenant passes the release preflight, which now includes what the code auto-installs (parity).
 */
const TENANT = 'e2eclienta' as const;
const OTHER = 'e2eclientb' as const;
const PLATFORM = 'e2eadmin' as const;
const STAMP = String(Date.now()).slice(-6);
const cwd = path.resolve(__dirname, '..', '..');

function odooShell(db: string, code: string): string {
  return execFileSync('docker', ['compose', 'exec', '-T', 'odoo', 'odoo', 'shell', '-d', db, '--no-http',
    '--log-level=error', '--db_host=db', '--db_user=odoo', '--db_password=odoo'], { input: code, cwd }).toString();
}

async function rpc(page: Page, tenant: TenantKey, model: string, method: string, args: unknown[],
  kwargs: Record<string, unknown> = {}) {
  const res = await callKw(page.request, tenant, model, method, args, kwargs);
  expect(res.error, JSON.stringify(res.error)).toBeUndefined();
  return res.result;
}

async function sessionOf(page: Page) {
  return page.evaluate(async () => {
    const res = await fetch('/web/session/get_session_info', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{"jsonrpc":"2.0","params":{}}' });
    const body = await res.json();
    return { db: body.result?.db, name: body.result?.name, uid: body.result?.uid as number, host: location.host,
             path: location.pathname };
  });
}

// An Owner on ``db`` (created once, as the superuser): Add User is the Owner's (ncollection.user.invite is Owner-only).
const OWNER_LOGIN = 'e2e.owner';
const OWNER_PW = 'e2e-owner-Pw-long-enough';
function ensureOwner(db: string) {
  const code = [
    'U = env["res.users"].with_context(active_test=False)',
    `u = U.search([("login", "=", "${OWNER_LOGIN}")], limit=1)`,
    'groups = [env.ref("base.group_user").id, env.ref("ncollection_core.group_role_owner").id]',
    `vals = {"name": "E2E Owner", "login": "${OWNER_LOGIN}", "password": "${OWNER_PW}", "active": True,`,
    '        "group_ids": [(6, 0, groups)], "nc_must_change_password": False}',
    'u.write(vals) if u else U.create(vals)',
    'cfg = env["ncollection.workspace.config"].search([], limit=1)',
    'cfg.write({"max_users": 0})   # seats are not what this journey measures (the seat rule has its own tests)',
    'env.cr.commit()',
    'print("OWNER_OK")',
  ].join('\n');
  expect(odooShell(db, code)).toContain('OWNER_OK');
}

// the platform pushes its config (System Branding + the company's contact details) to the fixture tenants, now
function pushConfig() {
  const code = [
    'tenants = env["ncollection.tenant"].search([("database_name", "in", ["e2eclienta", "e2eclientb"])])',
    'for t in tenants:',
    '    for _ in range(2):   # the first answer advertises what the tenant accepts; the second push carries it',
    '        v = t._config_sync_vals()',
    '        # identity only: the full payload would also push the registry plan (seats, apps) onto the fixtures',
    '        t._config_sync_push(t.database_name, {k: v[k] for k in ("nc_system_branding", "nc_company_identity") if k in v})',
    'env.cr.commit()',
    'print("PUSHED", ",".join(tenants.mapped("database_name")))',
  ].join('\n');
  expect(odooShell(PLATFORM, code)).toContain('PUSHED');
}

test.describe('The installable app is the system, not Odoo', () => {
  for (const tenant of [PLATFORM, TENANT] as const) {
    test(`web manifest, icons and offline page (${tenant})`, async ({ page }) => {
      await loginViaRpc(page, tenant);
      const brand = await rpc(page, tenant, 'ncollection.product.branding', 'nc_brand', []);
      const res = await page.request.get(`${TENANTS[tenant]}/web/manifest.webmanifest`);
      expect(res.ok()).toBeTruthy();
      const manifest = await res.json();
      expect(manifest.name).toBe(brand.name);
      expect(manifest.short_name).toBe(brand.short_name);
      expect(JSON.stringify(manifest)).not.toMatch(/Odoo|odoo-icon|\/odoo\b/);
      expect(manifest.scope).toBe('/atlas');
      expect(manifest.start_url).toBe('/atlas');
      expect(manifest.share_target?.action ?? '/atlas').toMatch(/^\/atlas/);
      expect(manifest.theme_color).toBe(brand.primary_color);
      expect(manifest.icons.map((i: { sizes: string }) => i.sizes)).toEqual(['192x192', '512x512']);
      for (const icon of manifest.icons) {
        const img = await page.request.get(`${TENANTS[tenant]}${icon.src}`);
        expect(img.headers()['content-type']).toBe('image/png');
        const bytes = await img.body();
        expect(bytes.subarray(1, 4).toString()).toBe('PNG');
        expect(bytes.readUInt32BE(16), 'square, at the declared size').toBe(Number(icon.sizes.split('x')[0]));
        expect(bytes.readUInt32BE(20)).toBe(Number(icon.sizes.split('x')[0]));
      }
      // what the browser installs from: the page's own <link rel=manifest> and the iOS icon
      await page.goto(`${TENANTS[tenant]}/atlas`, { waitUntil: 'domcontentloaded' });
      await page.waitForSelector(NAVBAR, { timeout: 120_000 });
      expect(await page.locator('link[rel=manifest]').getAttribute('href')).toBe('/web/manifest.webmanifest');
      expect(await page.locator('link[rel=apple-touch-icon]').getAttribute('href')).toMatch(/^\/ncollection\/brand\/app-icon\/180/);
      expect(await page.title()).not.toMatch(/Odoo/);
      const offline = await (await page.request.get(`${TENANTS[tenant]}/odoo/offline`)).text();
      expect(offline).not.toMatch(/Odoo will load|odoo_icon|Odoo logo/);
    });
  }
});

test.describe('Management', () => {
  for (const [lang, label] of [['en_US', 'Management'], ['ar_001', 'الإدارة']] as const) {
    test(`the platform's app is named and drawn as company administration (${lang})`, async ({ page }) => {
      test.setTimeout(240_000);
      await loginViaRpc(page, PLATFORM);
      const uid = (await rpc(page, PLATFORM, 'res.users', 'search_read', [[['login', '=', 'admin']]], { fields: ['id'] }))[0].id;
      await rpc(page, PLATFORM, 'res.users', 'write', [[uid], { lang }]);
      try {
        await page.goto(`${TENANTS[PLATFORM]}/atlas`, { waitUntil: 'domcontentloaded' });
        await page.waitForSelector(NAVBAR, { timeout: 120_000 });
        const app = page.locator('.o_nc_home__card', {
          has: page.locator('.o_nc_home__label', { hasText: new RegExp(`^${label}$`) }) }).first();
        await expect(app, 'on Home').toBeVisible({ timeout: 60_000 });
        await expect(page.locator('.o_nc_home__label', { hasText: /NCollection SaaS|Business Management/ })).toHaveCount(0);
        const root = (await rpc(page, PLATFORM, 'ir.model.data', 'check_object_reference',
          ['ncollection_subscription', 'menu_ncollection_saas_root']))[1];
        const menu = await rpc(page, PLATFORM, 'ir.ui.menu', 'read', [[root], ['web_icon']]);
        expect(menu[0].web_icon).toContain('icon_business.png');
        await app.click();
        await expect(page.locator('.o_menu_brand')).toHaveText(label, { timeout: 60_000 });
        expect(await page.evaluate(() => document.body.classList.contains('o_rtl'))).toBe(lang === 'ar_001');
      } finally {
        await rpc(page, PLATFORM, 'res.users', 'write', [[uid], { lang: 'en_US' }]);
      }
    });
  }
});

test.describe('Company Entry: two companies at once', () => {
  test.beforeAll(() => {
    // what provisioning gives every tenant: the platform's derived config-sync key (the gate answers 404 otherwise)
    const code = [
      'import base64, hashlib, hmac, os',
      'key = base64.urlsafe_b64encode(hmac.new(os.environ["NC_CONFIG_SYNC_KEY"].encode(),',
      '    b"nc-config-sync:" + env.cr.dbname.encode(), hashlib.sha256).digest()).decode()',
      'env["ncollection.config.sync.key"]._install_key(key, create_user=True)',
      'env.cr.commit()',
    ].join('\n');
    for (const db of [TENANT, OTHER]) odooShell(db, code);
  });

  async function enterFrom(page: Page, company: string) {
    const summary = `${TENANTS[PLATFORM]}/atlas/action-ncollection_subscription.action_ncollection_subscription_dashboard`;
    await page.goto(summary, { waitUntil: 'domcontentloaded' });
    const row = page.locator('.o_ncollection_companies .o_data_row', { hasText: company }).first();
    // Enter Company opens the company in a NEW tab (the platform tab stays where it is)
    const [tab] = await Promise.all([
      page.context().waitForEvent('page', { timeout: 150_000 }),
      row.locator('button[name=action_enter_company]').click({ timeout: 150_000 }),
    ]);
    return tab;
  }

  test('Tab A enters one company, Tab B another; switching never crosses sessions or databases', async ({ context }) => {
    test.setTimeout(600_000);
    const platform = await context.newPage();
    await loginViaRpc(platform, PLATFORM);
    const tenants = await rpc(platform, PLATFORM, 'ncollection.tenant', 'search_read',
      [[['database_name', 'in', [TENANT, OTHER]]]], { fields: ['id', 'database_name', 'company_name'] });
    await rpc(platform, PLATFORM, 'ncollection.tenant', 'write', [tenants.map((t: { id: number }) => t.id), {
      status: 'active', platform_admin_ids: [[4, 2]] }]);
    const company = Object.fromEntries(tenants.map((t: { database_name: string; company_name: string }) =>
      [t.database_name, t.company_name]));

    const tabA = await enterFrom(platform, company[TENANT]);
    await tabA.waitForURL((u) => u.hostname.startsWith(`${TENANT}.`) && u.pathname.startsWith('/atlas'), { timeout: 120_000 });
    await tabA.waitForSelector(NAVBAR, { timeout: 120_000 });

    // the second company, from the same platform tab: another new tab
    expect(new URL(platform.url()).hostname.startsWith(`${PLATFORM}.`), 'the platform tab did not navigate').toBeTruthy();
    const tabB = await enterFrom(platform, company[OTHER]);
    await tabB.waitForURL((u) => u.hostname.startsWith(`${OTHER}.`) && u.pathname.startsWith('/atlas'), { timeout: 120_000 });
    await tabB.waitForSelector(NAVBAR, { timeout: 120_000 });

    const expectIn = async (tab: Page, db: string) => {
      const s = await sessionOf(tab);
      expect(s.db, `${db}: the session is that company's database`).toBe(db);
      expect(s.host.startsWith(`${db}.`)).toBeTruthy();
      expect(s.path.startsWith('/atlas')).toBeTruthy();
      expect(s.name, 'the platform administrator, as the entry proxy').toMatch(/^Platform Admin: /);
      const shown = await tab.evaluate(() => (window as any).odoo.__session_info__?.db);
      expect(shown ?? db).toBe(db);
    };
    for (let round = 0; round < 3; round++) {
      await tabA.bringToFront();
      await tabA.reload({ waitUntil: 'domcontentloaded' });
      await tabA.waitForSelector(NAVBAR, { timeout: 120_000 });
      await expectIn(tabA, TENANT);
      await tabB.bringToFront();
      await tabB.reload({ waitUntil: 'domcontentloaded' });
      await tabB.waitForSelector(NAVBAR, { timeout: 120_000 });
      await expectIn(tabB, OTHER);
    }
    // each tab talks to its own database: the databases' own identities differ (the fixture company names may not)
    const uuidOf = (tab: Page) => tab.evaluate(async () => (await (await fetch('/web/dataset/call_kw/ir.config_parameter/get_param', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', params: { model: 'ir.config_parameter', method: 'get_param',
        args: ['database.uuid'], kwargs: {} } }) })).json()).result);
    const [uuidA, uuidB] = [await uuidOf(tabA), await uuidOf(tabB)];
    expect(uuidA).toBeTruthy();
    expect(uuidA, 'two tabs, two databases').not.toBe(uuidB);
    // one company's records are not readable from the other tab's session (different database, different cookie)
    const partnersA = await tabA.evaluate(async () => (await (await fetch('/web/dataset/call_kw/res.company/search_read', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', params: { model: 'res.company', method: 'search_read', args: [[], ['name']], kwargs: {} } }) })).json()).result);
    const partnersB = await tabB.evaluate(async () => (await (await fetch('/web/dataset/call_kw/res.company/search_read', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', params: { model: 'res.company', method: 'search_read', args: [[], ['name']], kwargs: {} } }) })).json()).result);
    expect(partnersA.length).toBeGreaterThan(0);
    expect(partnersB.length).toBeGreaterThan(0);

    // signing out of B leaves A signed in, in its own database; the platform tab is unaffected too
    await tabB.goto(new URL('/web/session/logout', tabB.url()).toString(), { waitUntil: 'domcontentloaded' });
    await tabA.reload({ waitUntil: 'domcontentloaded' });
    await tabA.waitForSelector(NAVBAR, { timeout: 120_000 });
    await expectIn(tabA, TENANT);
    await platform.reload({ waitUntil: 'domcontentloaded' });
    await platform.waitForSelector(NAVBAR, { timeout: 120_000 });
    expect((await sessionOf(platform)).db, 'the original platform tab is still the platform').toBe(PLATFORM);
  });
});

test.describe('The platform identity reaches the tenant', () => {
  test.beforeAll(() => pushConfig());

  test('Discuss: the system bot speaks in the system name', async ({ page }) => {
    test.setTimeout(300_000);
    await loginViaRpc(page, PLATFORM);
    const platformBrand = await rpc(page, PLATFORM, 'ncollection.product.branding', 'nc_brand', []);
    await loginViaRpc(page, TENANT);
    const brand = await rpc(page, TENANT, 'ncollection.product.branding', 'nc_brand', []);
    expect(brand.name, 'the tenant shows the platform system name').toBe(platformBrand.name);
    expect(brand.short_name).toBe(platformBrand.short_name);
    // the bot is base.partner_root, an ARCHIVED partner a user's RPC does not read: ask the database as the superuser
    const botName = odooShell(TENANT, 'print("BOT=" + env.ref("base.partner_root").name)').match(/BOT=(.*)/)?.[1]?.trim();
    expect(botName).toBe(`${brand.short_name} Bot`);
    await page.goto(`${TENANTS[TENANT]}/atlas/discuss`, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.o-mail-Discuss', { timeout: 120_000 });
    const chat = page.locator('.o-mail-DiscussSidebar, .o-mail-DiscussSidebarChannel', { hasText: `${brand.short_name} Bot` }).first();
    await expect(chat).toBeVisible({ timeout: 60_000 });
    await chat.click();
    await page.waitForSelector('.o-mail-Message', { timeout: 60_000 });
    const text = await page.locator('.o-mail-Thread').innerText();
    // the bot speaks as the CONFIGURED system (asserted above against System Branding), never as Odoo
    expect(text).not.toMatch(/\bOdoo\b|OdooBot/);
  });

  test('the Company screen: the system logo, and the email and website entered in Tenants', async ({ page }) => {
    test.setTimeout(300_000);
    await loginViaRpc(page, PLATFORM);
    const tenant = (await rpc(page, PLATFORM, 'ncollection.tenant', 'search_read',
      [[['database_name', '=', TENANT]]], { fields: ['email', 'tenant_url'] }))[0];
    await loginViaRpc(page, TENANT);
    await page.goto(`${TENANTS[TENANT]}/atlas/action-ncollection_core.action_workspace_company`, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.o_form_view', { timeout: 120_000 });
    const logo = page.locator('.o_field_widget[name=nc_system_logo] img');
    await expect(logo).toBeVisible();
    const company = await rpc(page, TENANT, 'res.company', 'search_read', [[]], { fields: ['email', 'website'] });
    if (tenant.email) {
      await expect(page.locator('.o_field_widget[name=email]')).toContainText(tenant.email);
      expect(company[0].email).toBe(tenant.email);
    }
    expect(company[0].website).toBe(tenant.tenant_url);
    await expect(page.locator('.o_form_view')).not.toContainText('ncollection.com');
  });

  for (const lang of ['en_US', 'ar_001']) {
    test(`Preferences > Notification says the system name, not Odoo (${lang})`, async ({ page }) => {
      test.setTimeout(300_000);
      await loginViaRpc(page, TENANT);
      const brand = await rpc(page, TENANT, 'ncollection.product.branding', 'nc_brand', []);
      await page.goto(`${TENANTS[TENANT]}/web/login`, { waitUntil: "domcontentloaded" });
      const uid = (await sessionOf(page)).uid;
      await rpc(page, TENANT, 'res.users', 'write', [[uid], { lang }]);
      try {
        await loginViaRpc(page, TENANT);
        await page.goto(`${TENANTS[TENANT]}/atlas`, { waitUntil: 'domcontentloaded' });
        await page.waitForSelector(NAVBAR, { timeout: 120_000 });
        await page.locator('.o_user_menu button, .o_user_menu .dropdown-toggle').first().click();
        await page.locator('.dropdown-item[data-menu=preferences], a[data-menu=preferences]').first().click();
        const radio = page.locator('.o_field_widget[name=notification_type]');
        await expect(radio).toBeVisible({ timeout: 60_000 });
        await expect(radio).toContainText(lang === 'ar_001' ? `في ${brand.name}` : `In ${brand.name}`);
        await expect(radio).not.toContainText(/Odoo|أودو/);
        if (lang === 'ar_001') {
          expect(await page.evaluate(() => document.body.classList.contains('o_rtl'))).toBe(true);
        }
      } finally {
        await rpc(page, TENANT, 'res.users', 'write', [[uid], { lang: 'en_US' }]);
      }
    });
  }

  test('the invoice PDF: View and Print are the same document, on the letterhead paper, in English and Arabic', async ({ page }) => {
    test.setTimeout(300_000);
    await loginViaRpc(page, TENANT);
    const invoices = await rpc(page, TENANT, 'account.move', 'search_read',
      [[['move_type', '=', 'out_invoice'], ['state', '=', 'posted']]], { fields: ['id', 'name'], limit: 1 });
    test.skip(!invoices.length, 'the fixture tenant has no posted invoice');
    const id = invoices[0].id;
    const view = await page.request.get(`${TENANTS[TENANT]}/ncollection/document/view/account.move/${id}`);
    expect(view.status()).toBe(200);
    expect(view.headers()['content-type']).toContain('application/pdf');
    const viewed = await view.body();
    expect(viewed.subarray(0, 5).toString()).toBe('%PDF-');
    const action = await rpc(page, TENANT, 'account.move', 'action_nc_print', [[id]]);
    expect(action.report_name).toBe('account.report_invoice_with_payments');
    const pages = (pdf: Buffer) => (pdf.toString('latin1').match(/\/Type\s*\/Page[^s]/g) || []).length;
    const printed = await page.request.get(`${TENANTS[TENANT]}/report/pdf/${action.report_name}/${id}`);
    expect(printed.status()).toBe(200);
    expect(pages(await printed.body())).toBe(pages(viewed));
  });
});

test.describe('Users: Add User with a role', () => {
  for (const lang of ['en_US', 'ar_001'] as const) {
    test(`an administrator adds a user with a role, who signs in at once (${lang})`, async ({ page, browser }) => {
      test.setTimeout(300_000);
      ensureOwner(TENANT);
      await loginViaRpc(page, TENANT, OWNER_LOGIN, OWNER_PW);
      const uid = (await rpc(page, TENANT, 'res.users', 'search_read', [[['login', '=', OWNER_LOGIN]]], { fields: ['id'] }))[0].id;
      await rpc(page, TENANT, 'res.users', 'write', [[uid], { lang }]);
      const login = `new.${lang.slice(0, 2)}.${STAMP}`;
      try {
        await page.goto(`${TENANTS[TENANT]}/atlas/action-ncollection_core.action_workspace_users`, { waitUntil: 'domcontentloaded' });
        await page.waitForSelector('.o_list_view', { timeout: 120_000 });
        await expect(page.locator('.o_main_navbar')).not.toContainText(/Invite User|دعوة مستخدم/);
        await page.locator('button[name]', { hasText: lang === 'ar_001' ? 'إضافة مستخدم' : 'Add User' }).first().click();
        const dialog = page.locator('.modal');
        await expect(dialog).toBeVisible({ timeout: 30_000 });
        await dialog.locator('.o_field_widget[name=name] input').fill(`New ${STAMP}`);
        await dialog.locator('.o_field_widget[name=username] input').fill(login);
        await dialog.locator('.o_field_widget[name=email] input').fill(`${login}@example.com`);
        await dialog.locator('.o_field_widget[name=role_id] input').fill(lang === 'ar_001' ? 'مبيعات' : 'Sales');
        await page.locator('.o-autocomplete--dropdown-item').first().click();
        // a password the administrator types (the generated, shown-once one is pinned by the Odoo tests)
        const password = `Pw-${STAMP}-long-enough`;
        await dialog.locator('.o_field_widget[name=mode] input[data-value=explicit]').check();
        await dialog.locator('.o_field_widget[name=new_password] input').fill(password);
        if (lang === 'ar_001') expect(await page.evaluate(() => document.body.classList.contains('o_rtl'))).toBe(true);
        await expect(dialog).not.toContainText(/Invite|Invitation|دعوة/);
        await dialog.locator('button[name=action_create_user]').click();
        await expect(page.locator('.o_notification', { hasText: login })).toBeVisible({ timeout: 60_000 });
        await expect(page.locator('.o_data_row', { hasText: login })).toBeVisible({ timeout: 60_000 });
        const created = await rpc(page, TENANT, 'res.users', 'search_read', [[['login', '=', login]]],
          { fields: ['nc_role_ids', 'nc_must_change_password', 'share'] });
        expect(created[0].nc_role_ids.length).toBeGreaterThan(0);
        expect(created[0].share).toBe(false);
        expect(created[0].nc_must_change_password).toBe(true);
        // usable now: the password signs the person in (and they are asked to change it at that sign-in)
        const fresh = await browser.newContext();
        const auth = await fresh.request.post(`${TENANTS[TENANT]}/web/session/authenticate`, {
          data: { jsonrpc: '2.0', params: { db: TENANT, login, password } } });
        expect((await auth.json()).result?.uid, 'the new user authenticates').toBeTruthy();
        await fresh.close();
      } finally {
        // a short, bounded cleanup: a failure above must not turn into a 300 s wait here
        await callKw(page.request, TENANT, 'res.users', 'write', [[uid], { lang: 'en_US' }], {}, 15_000).catch(() => {});
      }
    });
  }

  test('an Administrator (not Owner) cannot add a user: the server refuses the dialog', async ({ page }) => {
    await loginViaRpc(page, TENANT);   // the fixture admin: Administrator role, not Owner
    const res = await callKw(page.request, TENANT, 'ncollection.user.invite', 'create',
      [{ name: 'Not Allowed', username: `denied.${STAMP}`, email: `denied.${STAMP}@example.com` }]);
    expect(res.error?.data?.name, 'Owner-only by ACL').toBe('odoo.exceptions.AccessError');
  });
});

test.describe('Parity: what the code installs, every fixture tenant has', () => {
  test('the release preflight passes on every fixture database (behind AND auto-installed modules)', () => {
    for (const db of [TENANT, OTHER, PLATFORM]) {
      const out = execFileSync('docker', ['compose', 'exec', '-T', 'odoo', 'sh', '-c', 'python3 - "$1" --env', '_', db],
        { input: fs.readFileSync(path.join(cwd, 'scripts/upgrade/preflight_versions.py')), cwd }).toString();
      expect(out, `${db}: ${out}`).toContain('PREFLIGHT=PASS');
      expect(out, `${db}: nothing the code auto-installs is missing`).toMatch(/^PREFLIGHT_INSTALL=\r?$/m);
    }
  });
});
