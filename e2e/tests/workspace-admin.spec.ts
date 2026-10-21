import { test, expect, Page } from '@playwright/test';
import { activateLanguage, callKw, loginViaRpc, menuVisible, TENANTS } from '../fixtures/tenants';

/**
 * Workspace Administration Center (e2eclienta; admin = workspace Owner), English and Arabic.
 *  - every screen of the center opens without an uncaught error and without an error dialog;
 *  - Roles & Permissions lists the standard roles and "What this role allows" is read from the access rows;
 *  - an administrator resets a user's password: a temporary password is shown once, the person is sent to the change
 *    page on the first request, chooses their own password, and signs in with it (synthetic users, throwaway passwords);
 *  - a person without the Administrator role has no Workspace Administration and cannot manage roles over RPC.
 */
const TENANT = 'e2eclienta' as const;
const STAMP = String(Date.now()).slice(-7);

function collectErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  return errors;
}

async function menuActions(page: Page, lang = 'en_US'): Promise<Record<string, string>> {
  const rows = (await callKw(page.request, TENANT, 'ir.ui.menu', 'search_read',
    [[['parent_id.name', 'in', ['Workspace Administration', 'إدارة مساحة العمل', 'إعدادات مساحة العمل']]]],
    { fields: ['name', 'action'], context: { lang } })).result;
  const out: Record<string, string> = {};
  for (const row of rows) if (row.action) out[row.name] = String(row.action).split(',')[1];
  return out;
}

async function makeUser(page: Page, login: string, password: string, roleCodes: string[] = []): Promise<number> {
  const group = (await callKw(page.request, TENANT, 'ir.model.data', 'check_object_reference', ['base', 'group_user'])).result[1];
  const created = await callKw(page.request, TENANT, 'res.users', 'create',
    [{ name: `E2E ${login}`, login, password, group_ids: [[6, 0, [group]]] }]);
  expect(created.error, JSON.stringify(created.error)).toBeUndefined();
  if (roleCodes.length) {
    const roles = (await callKw(page.request, TENANT, 'ncollection.role', 'search_read',
      [[['code', 'in', roleCodes]]], { fields: ['id'] })).result;
    const set = await callKw(page.request, TENANT, 'res.users', 'nc_set_roles', [[created.result], roles.map((r: { id: number }) => r.id)]);
    expect(set.error, JSON.stringify(set.error)).toBeUndefined();
  }
  return created.result;
}

test.describe('Workspace Administration Center', () => {
  test.beforeEach(async ({ page }) => {
    await loginViaRpc(page, TENANT);
  });

  // whatever a test did, the fixture admin goes back to English: later tests read English labels
  test.afterEach(async ({ page }) => {
    await loginViaRpc(page, TENANT);
    await callKw(page.request, TENANT, 'res.users', 'write', [[2], { lang: 'en_US' }]);
  });

  test('an administrator resets a password: shown once, forced change, own password works', async ({ page, browser }) => {
    test.setTimeout(240_000);              // the dev server is single-process: under a full parallel run one answer can take a minute
    const errors = collectErrors(page);
    const login = `e2epw${STAMP}`;
    const given = `Xy${STAMP}-${Math.random().toString(36).slice(2, 10)}A1!`;
    const userId = await makeUser(page, login, given);
    const actions = await menuActions(page);
    await page.goto(`${TENANTS[TENANT]}/odoo/action-${actions['Users']}/${userId}`, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.o_form_view', { timeout: 60_000 });
    await page.getByRole('button', { name: 'Reset password' }).first().click();
    await page.waitForSelector('.modal .o_form_view', { timeout: 30_000 });
    await page.locator('.modal-footer').getByRole('button', { name: 'Reset password' }).click();
    const notice = page.locator('.o_notification');
    await expect(notice).toContainText('Temporary password for', { timeout: 30_000 });
    const text = (await notice.first().innerText()).replace(/\s+/g, ' ');
    const temporary = /: (\S+) - give it to them now/.exec(text)?.[1];
    expect(temporary && temporary.length >= 16, 'a generated password is shown').toBeTruthy();

    // the person, in a clean browser context: the temporary password signs in, and nothing but the change page opens
    const context = await browser.newContext();
    const person = await context.newPage();
    await loginViaRpc(person, TENANT, login, temporary!);
    const first = await person.request.get(`${TENANTS[TENANT]}/odoo`, { maxRedirects: 0 });
    expect(first.status()).toBe(303);
    expect(first.headers()['location']).toContain('/ncollection/password/change');
    const blocked = await callKw(person.request, TENANT, 'res.partner', 'search_count', [[]]);
    expect(JSON.stringify(blocked)).toContain('choose a new password');
    await person.goto(`${TENANTS[TENANT]}/ncollection/password/change`, { waitUntil: 'domcontentloaded' });
    // the sign-in layout's own scripts may reload the page once (translations): let it settle before typing into it
    await person.waitForLoadState('networkidle');
    await expect(person.locator('body')).toContainText('Choose your own password');
    const chosen = `Nn${STAMP}-${Math.random().toString(36).slice(2, 10)}B2!`;
    await person.fill('input[name=old_password]', temporary!);
    await person.fill('input[name=password]', chosen);
    await person.fill('input[name=confirm_password]', chosen);
    // What is asserted is the server's answer to the change (a redirect to the sign-in page), not the sign-in page
    // itself: the edge throttles /web/login (10 r/m), which a full run exhausts, and the page is not the point here.
    const [answer] = await Promise.all([
      person.waitForResponse((r) => r.url().includes('/ncollection/password/change') && r.request().method() === 'POST',
        { timeout: 180_000 }),
      person.locator('form').evaluate((form: HTMLFormElement) => form.requestSubmit()),   // the button's own action
    ]);
    expect(answer.status()).toBe(303);
    expect(answer.headers()['location']).toContain('/web/login');
    await loginViaRpc(person, TENANT, login, chosen);
    const after = await person.request.get(`${TENANTS[TENANT]}/odoo`, { maxRedirects: 0 });
    expect(after.status()).toBe(200);
    await context.close();

    // the audit trail (ncollection_auth, installed with every provisioned tenant) records both events, without any secret
    const hasAudit = (await callKw(page.request, TENANT, 'ir.model', 'search_count', [[['model', '=', 'ncollection.auth.log']]])).result;
    if (hasAudit) {
      const rows = (await callKw(page.request, TENANT, 'ncollection.auth.log', 'search_read',
        [[['event_type', 'in', ['admin_password_reset', 'password_changed']]]],
        { fields: ['event_type', 'detail', 'login'], order: 'id desc', limit: 6 })).result;
      expect(rows.map((r: { event_type: string }) => r.event_type)).toEqual(expect.arrayContaining(['admin_password_reset', 'password_changed']));
      expect(JSON.stringify(rows)).not.toContain(temporary!);
      expect(JSON.stringify(rows)).not.toContain(chosen);
    }
    expect(errors, 'no uncaught page error').toEqual([]);
  });

  test('every screen opens cleanly, in English and in Arabic', async ({ page }) => {
    const errors = collectErrors(page);
    for (const lang of ['en_US', 'ar_001']) {
      await activateLanguage(page.request, TENANT, lang);
      await callKw(page.request, TENANT, 'res.users', 'write', [[2], { lang }]);
      await loginViaRpc(page, TENANT);                           // a NEW session picks the language up
      const actions = await menuActions(page, lang);
      expect(Object.keys(actions).length, `menus in ${lang}: ${JSON.stringify(actions)}`).toBeGreaterThanOrEqual(8);
      for (const [name, id] of Object.entries(actions)) {
        if (name === 'Invite User' || name === 'دعوة مستخدم') continue;   // the wizard is Owner-only by its ACL; the fixture admin is a system user, not the Owner role
        await page.goto(`${TENANTS[TENANT]}/odoo/action-${id}`, { waitUntil: 'domcontentloaded' });
        await page.waitForSelector('.o_main_navbar', { timeout: 60_000 });
        await page.waitForTimeout(2500);                  // a screen, or the notice a not-installed one shows
        await expect(page.locator('.o_error_dialog'), `${name} (${lang})`).toHaveCount(0);
        await expect(page.locator('.o_action_manager')).not.toContainText('Odoo Server Error');
      }
      if (lang === 'ar_001') {
        expect(await page.evaluate(() => getComputedStyle(document.body).direction)).toBe('rtl');
        expect(Object.keys(actions).some((n) => /[؀-ۿ]/.test(n)), 'Arabic menu names').toBeTruthy();
      }
    }
    expect(errors, 'no uncaught page error').toEqual([]);
  });

  test('Roles & Permissions lists the standard roles and shows what a role really allows', async ({ page }) => {
    const errors = collectErrors(page);
    const actions = await menuActions(page);
    await page.goto(`${TENANTS[TENANT]}/odoo/action-${actions['Roles & Permissions']}`, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.o_list_view .o_data_row', { timeout: 60_000 });
    const list = page.locator('.o_list_view');
    for (const name of ['Administrator', 'Finance Manager', 'Accountant', 'Reports Viewer']) {
      await expect(list).toContainText(name);
    }
    await page.locator('.o_data_row', { hasText: 'Accountant' }).first().locator('td[name=name]').click();
    await page.waitForSelector('.o_form_view', { timeout: 30_000 });
    await page.getByRole('button', { name: 'What this role allows' }).click();
    await page.waitForSelector('.o_list_view .o_data_row', { timeout: 30_000 });
    expect(await page.locator('.o_data_row').count()).toBeGreaterThan(5);
    await expect(page.locator('.o_list_view thead')).toContainText('Edit');
    await expect(page.locator('.o_error_dialog')).toHaveCount(0);
    expect(errors, 'no uncaught page error').toEqual([]);
  });

  test('a person without the Administrator role has no Workspace Administration and cannot manage roles', async ({ page, browser }) => {
    const login = `e2esales${STAMP}`;
    const password = `Sa${STAMP}-${Math.random().toString(36).slice(2, 10)}C3!`;
    await makeUser(page, login, password, ['sales_user']);
    const context = await browser.newContext();
    const person = await context.newPage();
    await loginViaRpc(person, TENANT, login, password);
    expect(await menuVisible(person.request, TENANT, 'Workspace Administration')).toBe(false);
    const create = await callKw(person.request, TENANT, 'ncollection.role', 'create', [{ name: 'Mine', custom_group_ids: [[6, 0, []]] }]);
    expect(create.error, 'creating a role is refused').toBeTruthy();
    const grant = await callKw(person.request, TENANT, 'res.users', 'nc_set_roles', [[2], []]);
    expect(grant.error, 'changing another user\'s roles is refused').toBeTruthy();
    const service = await callKw(person.request, TENANT, 'ncollection.admin.service', 'list_users', [{ actor: { ref: 'u1', name: 'x' } }]);
    expect(service.error, 'the platform service is not for workspace users').toBeTruthy();
    await context.close();
  });
});
