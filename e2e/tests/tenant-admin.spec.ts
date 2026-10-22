import { test, expect, Page } from '@playwright/test';
import { authenticate, callKw, loginViaRpc, TENANTS } from '../fixtures/tenants';

/**
 * Tenant Administration, platform side (e2eadmin = the platform database).
 *  - the tenant form is organised as Overview, Subscription, Users & Access, Platform Access, System, Modules, Configuration Sync;
 *  - Users & Access shows the Tenant Administrator, and says the users can be managed once the workspace is provisioned
 *    (only the provisioning engine can mark a database ready, so a synthetic tenant here is never 'ready');
 *  - a platform administrator who is not on the tenant's allow-list is refused with a clear message.
 * The channel into a really provisioned tenant (reset, forced change, limits, audit) is proven by
 * ncollection_saas/scripts/provisioning/verify_tenant_admin.sh; synthetic records only here.
 */
const PLATFORM = 'e2eadmin' as const;
const STAMP = String(Date.now()).slice(-7);

function collectErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  return errors;
}

test.describe('Tenant Administration (platform)', () => {
  let tenantId = 0;
  let outsiderLogin = '';
  const outsiderPassword = `Pl${STAMP}-${Math.random().toString(36).slice(2, 10)}D4!`;

  test.beforeAll(async ({ playwright }) => {
    const request = await playwright.request.newContext();
    await authenticate(request, PLATFORM, 'admin', 'admin');
    const created = await callKw(request, PLATFORM, 'ncollection.tenant', 'create', [{
      company_name: `Tenant Admin E2E ${STAMP}`, database_name: `e2eunreach${STAMP}`, admin_login: `e2eadm${STAMP}`,
      status: 'active', platform_admin_ids: [[6, 0, [2]]],
    }]);
    expect(created.error, JSON.stringify(created.error)).toBeUndefined();
    tenantId = created.result;
    const group = (await callKw(request, PLATFORM, 'ir.model.data', 'check_object_reference',
      ['ncollection_subscription', 'group_platform_admin'])).result[1];
    const internal = (await callKw(request, PLATFORM, 'ir.model.data', 'check_object_reference', ['base', 'group_user'])).result[1];
    outsiderLogin = `e2eoutsider${STAMP}`;
    const user = await callKw(request, PLATFORM, 'res.users', 'create', [{
      name: 'E2E Outsider', login: outsiderLogin, password: outsiderPassword, group_ids: [[6, 0, [internal, group]]],
    }]);
    expect(user.error, JSON.stringify(user.error)).toBeUndefined();
    await request.dispose();
  });

  test.afterAll(async ({ playwright }) => {
    const request = await playwright.request.newContext();
    await authenticate(request, PLATFORM, 'admin', 'admin');
    if (tenantId) await callKw(request, PLATFORM, 'ncollection.tenant', 'unlink', [[tenantId]]);
    const users = (await callKw(request, PLATFORM, 'res.users', 'search', [[['login', '=', outsiderLogin]]])).result;
    if (users?.length) await callKw(request, PLATFORM, 'res.users', 'write', [users, { active: false }]);
    await request.dispose();
  });

  test('the tenant form is organised in sections and Users & Access names the Tenant Administrator', async ({ page }) => {
    const errors = collectErrors(page);
    await loginViaRpc(page, PLATFORM);
    await page.goto(`${TENANTS[PLATFORM]}/odoo/action-ncollection_subscription.action_ncollection_tenant/${tenantId}`,
      { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.o_form_view .o_notebook', { timeout: 60_000 });
    const tabs = (await page.locator('.o_notebook .nav-link').allInnerTexts()).map((t) => t.trim());
    expect(tabs).toEqual(['Overview', 'Subscription', 'Users & Access', 'Platform Access', 'System', 'Modules',
      'Configuration Sync']);
    await page.locator('.o_notebook .nav-link', { hasText: 'Users & Access' }).click();
    await expect(page.locator('.o_notebook')).toContainText(`e2eadm${STAMP}`);
    await expect(page.locator('.o_notebook')).toContainText('once its workspace has been provisioned');
    await expect(page.getByRole('button', { name: 'Users of this company' })).toBeHidden();
    await expect(page.locator('.o_error_dialog')).toHaveCount(0);
    expect(errors, 'no uncaught page error').toEqual([]);
  });

  test('a platform administrator who is not on the allow-list is refused', async ({ browser }) => {
    const context = await browser.newContext();
    const page = await context.newPage();
    await loginViaRpc(page, PLATFORM, outsiderLogin, outsiderPassword);
    const refused = await callKw(page.request, PLATFORM, 'ncollection.tenant', 'action_open_users', [[tenantId]]);
    expect(JSON.stringify(refused)).toContain('not authorised');
    const reset = await callKw(page.request, PLATFORM, 'ncollection.tenant', 'action_reset_admin_password', [[tenantId]]);
    expect(JSON.stringify(reset)).toContain('not authorised');
    await context.close();
  });
});
