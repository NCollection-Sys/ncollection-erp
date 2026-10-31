import { test, expect, Page, APIRequestContext } from '@playwright/test';
import { callKw, loginViaRpc, NAVBAR, TENANTS } from '../fixtures/tenants';

/**
 * UAE e-invoicing (ncollection_uae_einvoicing): the browser surface that exists, read-only, on the dedicated UAE
 * fixture `e2euae` (e2e/scripts/setup_e2e_uae_tenant.sh: AE books verified, engine enabled by the gated override,
 * owner `e2e.owner` holds the e-invoicing manager group, `biz` holds no e-invoicing group). It never runs against the
 * US fixtures e2eclienta / e2eclientb: the module is gated and cannot be installed there.
 *  - the owner reaches Accounting > UAE E-Invoicing > E-Invoices / Received E-Invoices, and Configuration;
 *  - an e-invoice opens on its form with the Scenario / Scope / Lines / Findings / Identity / History pages;
 *  - `biz` sees no e-invoicing menu AND is refused at the ORM (Rule 4: the hidden menu is not the control).
 *
 * Nothing here creates, prepares, validates or submits a document, and nothing reaches an ASP (no provider exists on
 * the fixture). When e2euae is absent or not ready, every test SKIPS with the reason: a skip is not a pass.
 */
const TENANT = 'e2euae' as const;
const MODULE = 'ncollection_uae_einvoicing';
const OWNER = 'e2e.owner';
const OWNER_PW = process.env.E2E_UAE_OWNER_PW ?? 'E2e-Uae-Owner-77!'; // dev-only fixture credential (setup script)
const PAGES = ['Scenario', 'Scope', 'Lines', 'Findings', 'Identity', 'History'];
// Menus by XML id, never by display name: a name can repeat anywhere in the menu tree.
const MENUS = ['menu_einvoice_root', 'menu_einvoice_documents', 'menu_einvoice_inbound', 'menu_einvoice_provider',
  'menu_einvoice_export', 'menu_einvoice_sb_agreement'];

function collectErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  return errors;
}

/** Why the fixture is not usable, or '' when it is: reachable, logged in, module installed. */
async function fixtureProblem(page: Page): Promise<string> {
  try {
    await loginViaRpc(page, TENANT, OWNER, OWNER_PW);
  } catch (err) {
    return `${TENANT} is not provisioned (run make e2e-uae-setup): ${(err as Error).message.slice(0, 120)}`;
  }
  const res = await callKw(page.request, TENANT, 'ir.module.module', 'search_count',
    [[['name', '=', MODULE], ['state', '=', 'installed']]]);
  return res.result === 1 ? '' : `${MODULE} is not installed on ${TENANT}`;
}

type MenuRef = { name: string; res_id: number };

/**
 * The menu ids, resolved with the owner's session: ir.model.data is readable only by Access Rights, so a restricted
 * user cannot resolve them and must be handed them.
 */
async function resolveMenus(request: APIRequestContext): Promise<MenuRef[]> {
  const res = await callKw(request, TENANT, 'ir.model.data', 'search_read',
    [[['module', '=', MODULE], ['model', '=', 'ir.ui.menu'], ['name', 'in', MENUS]], ['name', 'res_id']]);
  if (res.error) throw new Error(`menu ids not readable: ${JSON.stringify(res.error).slice(0, 200)}`);
  const ids: MenuRef[] = res.result ?? [];
  // an empty answer must never read as "no menu visible": every id has to resolve first
  if (ids.length !== MENUS.length) throw new Error(`resolved ${ids.length}/${MENUS.length} menu ids`);
  return ids;
}

/** The names of the given menus that the session's user can see (load_menus applies every filter). */
async function visibleMenus(request: APIRequestContext, ids?: MenuRef[]): Promise<string[]> {
  ids = ids ?? await resolveMenus(request);
  const loadRes = await callKw(request, TENANT, 'ir.ui.menu', 'load_menus', [false]);
  if (!loadRes.result) throw new Error(`load_menus failed: ${JSON.stringify(loadRes.error ?? {}).slice(0, 200)}`);
  const loaded = loadRes.result;
  const shown = new Set(Object.keys(loaded).map(Number));
  return ids.filter((r) => shown.has(r.res_id)).map((r) => r.name);
}

async function openAction(page: Page, xmlid: string, suffix = '') {
  await page.goto(`${TENANTS[TENANT]}/odoo/action-${MODULE}.${xmlid}${suffix}`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector(NAVBAR, { timeout: 60_000 });
}

/** A list action has rendered: either its list or the action's own no-content helper, and no error dialog. */
async function expectListRendered(page: Page) {
  await page.waitForSelector('.o_action_manager .o_list_view, .o_action_manager .o_view_nocontent', { timeout: 30_000 });
  await expect(page.locator('.o_error_dialog')).toHaveCount(0);
}

test.describe('UAE e-invoicing: the user interface', () => {
  test.beforeEach(async ({ page }) => {
    const problem = await fixtureProblem(page);
    test.skip(!!problem, problem);
  });

  test('the e-invoicing menus are under Accounting for an e-invoicing manager', async ({ page }) => {
    expect((await visibleMenus(page.request)).sort()).toEqual([...MENUS].sort());
  });

  test('E-Invoices and Received E-Invoices open without an error', async ({ page }) => {
    const errors = collectErrors(page);
    await openAction(page, 'action_einvoice');
    await expectListRendered(page);
    await openAction(page, 'action_einvoice_inbound');
    await expectListRendered(page);
    expect(errors, 'no uncaught page error').toEqual([]);
  });

  test('an e-invoice opens on its form with every page', async ({ page }) => {
    const errors = collectErrors(page);
    const ids = (await callKw(page.request, TENANT, 'ncollection.einvoice', 'search', [[]], { limit: 1 })).result ?? [];
    test.skip(!ids.length, `${TENANT} has no e-invoice to open (needs a dedicated e-invoice fixture record)`);
    await openAction(page, 'action_einvoice', `/${ids[0]}`);
    await page.waitForSelector('.o_form_view', { timeout: 30_000 });
    for (const label of PAGES) {
      await expect(page.locator('.o_form_view .o_notebook .nav-link', { hasText: label }).first()).toBeVisible();
    }
    await page.locator('.o_form_view .o_notebook .nav-link', { hasText: 'History' }).first().click();
    await expect(page.locator('.o_form_view .o_notebook .tab-pane.active')).toBeVisible();
    await expect(page.locator('.o_error_dialog')).toHaveCount(0);
    expect(errors, 'no uncaught page error').toEqual([]);
  });
});

test.describe('UAE e-invoicing: access control', () => {
  test('a user without the e-invoicing groups sees no menu and is refused by the ORM', async ({ page, browser }) => {
    const problem = await fixtureProblem(page);
    test.skip(!!problem, problem);
    const menus = await resolveMenus(page.request); // as the owner
    // a fresh context: no cookie of the owner's session can reach the restricted user's requests
    const ctx = await browser.newContext();
    try {
      const biz = await ctx.newPage();
      await loginViaRpc(biz, TENANT, 'biz', 'demo1234');
      const info = await callKw(biz.request, TENANT, 'res.users', 'search_read',
        [[['login', '=', 'biz']]], { fields: ['id'], limit: 1 });
      expect(info.error, 'the restricted session is a working session').toBeUndefined();
      expect(await visibleMenus(biz.request, menus)).toEqual([]);
      for (const model of ['ncollection.einvoice', 'ncollection.einvoice.inbound', 'ncollection.einvoice.secret']) {
        const res = await callKw(biz.request, TENANT, model, 'search_read', [[]], { fields: ['id'], limit: 1 });
        expect(res.result, `${model} must return no data`).toBeUndefined();
        expect(res.error?.data?.name, `${model} must be refused`).toBe('odoo.exceptions.AccessError');
      }
    } finally {
      await ctx.close();
    }
  });
});
