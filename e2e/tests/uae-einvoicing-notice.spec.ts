import { test, expect, Page } from '@playwright/test';

/**
 * UAE e-invoicing: the "XML generated, not yet validated" notice, on an ISOLATED acceptance server that runs the
 * current source (the shared stack may still run older code, so it is not evidence for a new view).
 *
 * Opt-in: runs only when NC_UAE_ACCEPT_URL names such a server (e.g. http://127.0.0.1:18069, a disposable database
 * whose only seeded documents are fictional), with NC_UAE_ACCEPT_LOGIN / NC_UAE_ACCEPT_PASSWORD (a disposable test
 * user) and the ids of one `locally_checked` (NC_UAE_ACCEPT_CHECKED_ID) and one `validated`
 * (NC_UAE_ACCEPT_VALIDATED_ID) e-invoice. Without them every test SKIPS with that reason: a skip is not a pass.
 * Nothing here prepares, generates, validates or submits anything.
 */
const URL = process.env.NC_UAE_ACCEPT_URL ?? '';
const LOGIN = process.env.NC_UAE_ACCEPT_LOGIN ?? '';
const PASSWORD = process.env.NC_UAE_ACCEPT_PASSWORD ?? '';
const CHECKED = process.env.NC_UAE_ACCEPT_CHECKED_ID ?? '';
const VALIDATED = process.env.NC_UAE_ACCEPT_VALIDATED_ID ?? '';
const PAGES = ['Scenario', 'Scope', 'Lines', 'Findings', 'Identity', 'Transport', 'History'];
const NOTICE_EN = 'The XML is generated, archived and locally checked. It has not been officially validated yet.';
const NOTICE_AR = 'تم إنشاء ملف XML وأرشفته وفحصه محلياً. لم يتم التحقق منه رسمياً بعد.';

async function rpc(page: Page, model: string, method: string, args: unknown[], kwargs = {}) {
  const res = await page.request.post(`${URL}/web/dataset/call_kw/${model}/${method}`, {
    data: { jsonrpc: '2.0', method: 'call', params: { model, method, args, kwargs } },
  });
  const body = await res.json();
  if (body.error) throw new Error(JSON.stringify(body.error).slice(0, 300));
  return body.result;
}

async function signIn(page: Page, lang: string) {
  // the login page's language cookie otherwise carries over from whatever the database defaults to
  await page.context().addCookies([{ name: 'frontend_lang', value: lang, url: URL }]);
  await page.goto(`${URL}/web/login`);
  await page.fill('input[name="login"]', LOGIN);
  await page.fill('input[name="password"]', PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForSelector('.o_main_navbar', { timeout: 60_000 });
}

/** A session takes its language at login: set the user's language, then sign in again. */
async function login(page: Page, lang = 'en_US') {
  await signIn(page, lang);
  const uid = (await page.request.post(`${URL}/web/session/get_session_info`, { data: {} }).then((r) => r.json()))
    .result.uid;
  await rpc(page, 'res.users', 'write', [[uid], { lang }]);
  await page.goto(`${URL}/web/session/logout`);
  await signIn(page, lang);
}

async function openEinvoice(page: Page, id: string) {
  await page.goto(`${URL}/odoo/action-ncollection_uae_einvoicing.action_einvoice/${id}`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.o_form_view .o_notebook', { timeout: 60_000 });
}

test.describe('UAE e-invoicing: the locally-checked notice (isolated acceptance server)', () => {
  test.skip(!URL || !LOGIN || !PASSWORD || !CHECKED || !VALIDATED,
    'NC_UAE_ACCEPT_* not set: no isolated acceptance server to run against');

  test('a locally checked e-invoice shows the notice, its seven pages and Validate, never Submit', async ({ page }) => {
    await login(page);
    const [rec] = await rpc(page, 'ncollection.einvoice', 'read', [[Number(CHECKED)], ['local_state']]);
    expect(rec.local_state).toBe('locally_checked');
    await openEinvoice(page, CHECKED);
    const notice = page.locator('.o_form_view .o_nc_einvoice_checked');
    await expect(notice).toBeVisible();
    await expect(notice).toHaveText(NOTICE_EN);
    for (const label of PAGES) {
      await expect(page.locator('.o_form_view .o_notebook .nav-link', { hasText: label }).first()).toBeVisible();
    }
    const header = page.locator('.o_form_view .o_form_statusbar');
    await expect(header.getByRole('button', { name: 'Validate', exact: true })).toBeVisible();
    await expect(header.getByRole('button', { name: 'Generate XML' })).toHaveCount(0);
    await expect(header.getByRole('button', { name: /Submit/ })).toHaveCount(0);
    await expect(header.locator('.o_statusbar_status .o_arrow_button_current')).toHaveText(/Ready/);
    await page.locator('.o_form_view .o_notebook .nav-link', { hasText: 'Transport' }).first().click();
    await expect(page.locator('.o_form_view .o_notebook .tab-pane.active')).toBeVisible();
    await expect(page.locator('.o_error_dialog')).toHaveCount(0);
  });

  test('a validated e-invoice does not show the notice', async ({ page }) => {
    await login(page);
    const [rec] = await rpc(page, 'ncollection.einvoice', 'read', [[Number(VALIDATED)], ['local_state']]);
    expect(rec.local_state).toBe('validated');
    await openEinvoice(page, VALIDATED);
    await expect(page.locator('.o_form_view .o_nc_einvoice_checked')).toHaveCount(0);
    await expect(page.locator('.o_form_view .o_form_statusbar .o_arrow_button_current')).toHaveText(/Validated/);
  });

  test('the notice is Arabic in an Arabic session', async ({ page }) => {
    await login(page, 'ar_001');
    await openEinvoice(page, CHECKED);
    await expect(page.locator('.o_form_view .o_nc_einvoice_checked')).toHaveText(NOTICE_AR);
    const uid = (await page.request.post(`${URL}/web/session/get_session_info`, { data: {} }).then((r) => r.json()))
      .result.uid;
    await rpc(page, 'res.users', 'write', [[uid], { lang: 'en_US' }]);
  });

  test('the notice stays visible on a phone-width screen', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await login(page);
    await openEinvoice(page, CHECKED);
    await expect(page.locator('.o_form_view .o_nc_einvoice_checked')).toBeVisible();
    const box = await page.locator('.o_form_view .o_nc_einvoice_checked').boundingBox();
    expect(box && box.width <= 390).toBeTruthy();
  });
});
