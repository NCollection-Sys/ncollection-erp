import { test, expect, type Page } from '@playwright/test';
import { TENANTS, authenticate, callKw, loginViaRpc } from '../fixtures/tenants';

/**
 * Plan module picker (#467) — browser coverage.
 *
 * This exists because the picker's behaviour lives entirely in an OWL widget,
 * and the repo has no JS unit runner. The Python suite can prove what the
 * CATALOG contains (test_module_catalog.py) but not what the bulk actions do
 * to the field, and those are the operations that can quietly revoke modules
 * from live tenants:
 *
 *   - "Clear selection" must not drop a licensed name the catalog does not
 *     offer (`ncollection_mis_templates` is exactly that, and ENTERPRISE
 *     licenses it today);
 *   - no bulk action may add or remove a CORE module, which provisioning
 *     installs whatever the plan says;
 *   - "Select visible results" must follow the search box, or its label lies.
 *
 * Driven against the e2eadmin platform DB (ncollection_saas, which brings
 * ncollection_subscription — see setup_e2e_tenants.sh). The scratch plan is
 * created and deleted over RPC and is NEVER SAVED from the form: a saved plan
 * write fans a config sync and a module-install job out to every ready tenant
 * on it, and a UI test must not be able to touch a tenant database. A plan with
 * no tenants has nothing to fan out to either way, so both guards hold.
 */

const PLATFORM = TENANTS.e2eadmin;
const PICKER = '.o_nc_modpick';
const CARD = '.o_nc_modpick__card';
// Selected state, as a class matcher: toHaveClass auto-waits, so assertions
// built on it cannot sample a half-rendered grid the way an immediate
// evaluateAll can.
const SELECTED = /o_nc_modpick__card--on/;

/** The field's raw value, read out of the widget's own DOM state. */
async function selectedModules(page: Page): Promise<string[]> {
  const names = await page
    .locator(`${CARD}--on[data-module]`)
    .evaluateAll((els) => els.map((el) => el.getAttribute('data-module') || ''));
  return names.filter(Boolean).sort();
}

test.describe('subscription plan module picker (#467)', () => {
  let planId: number;

  test.beforeAll(async ({ playwright }) => {
    const request = await playwright.request.newContext({ ignoreHTTPSErrors: true });
    await authenticate(request, 'e2eadmin', 'admin', 'admin');
    const created = await callKw(request, 'e2eadmin', 'ncollection.subscription.plan', 'create', [
      {
        name: 'E2E Picker Scratch',
        code: 'E2EPICKER',
        max_users: 5,
        // A name the picker's catalog does not offer (it is not an
        // application), so the "never dropped" assertion has a subject.
        allowed_module_names: 'ncollection_mis_templates',
      },
    ]);
    planId = created.result as number;
    expect(planId, 'scratch plan must be created').toBeTruthy();
    await request.dispose();
  });

  test.afterAll(async ({ playwright }) => {
    const request = await playwright.request.newContext({ ignoreHTTPSErrors: true });
    await authenticate(request, 'e2eadmin', 'admin', 'admin');
    await callKw(request, 'e2eadmin', 'ncollection.subscription.plan', 'unlink', [[planId]]);
    await request.dispose();
  });

  test.beforeEach(async ({ page }) => {
    await loginViaRpc(page, 'e2eadmin');
    await page.goto(
      `${PLATFORM}/odoo/action-ncollection_subscription.action_ncollection_subscription_plan/${planId}`,
      { waitUntil: 'domcontentloaded' },
    );
    // exact: the toolbar inside the widget also renders a tab called "All modules".
    await page.getByRole('tab', { name: 'Modules', exact: true }).click();
    // The catalog is fetched over RPC; cards only exist after it lands.
    await expect(page.locator(PICKER)).toBeVisible({ timeout: 30_000 });
    await expect(page.locator(CARD).first()).toBeVisible({ timeout: 20_000 });
  });

  test('the grid renders several cards per row at desktop width', async ({ page }) => {
    // The defect this replaces: cards stacked in a narrow column with the
    // right-hand half of the page empty. Asserted geometrically rather than by
    // class name, because a CSS regression is what would break it.
    await page.setViewportSize({ width: 1600, height: 900 });
    const cards = page.locator(CARD);
    await expect(cards).not.toHaveCount(0);
    const boxes = await cards.evaluateAll((els) =>
      els.slice(0, 8).map((el) => el.getBoundingClientRect().top),
    );
    const distinctRows = new Set(boxes.map((t) => Math.round(t)));
    expect(
      boxes.length - distinctRows.size,
      'at 1600px the first cards must share rows, not stack vertically',
    ).toBeGreaterThan(0);
  });

  test('the native accounting modules are offered', async ({ page }) => {
    await expect(
      page.locator(`${CARD}[data-module="ncollection_account_reports"]`),
    ).toHaveCount(1);
  });

  test('select all, then clear, keeps the module the catalog does not offer', async ({ page }) => {
    const reports = page.locator(`${CARD}[data-module="ncollection_account_reports"]`);

    await page.getByRole('button', { name: 'Select all' }).click();
    // Auto-waiting assertion, not a snapshot read: OWL re-renders on the next
    // frame, so an immediate evaluateAll can sample the DOM mid-update and
    // report either state.
    await expect(reports).toHaveClass(SELECTED);
    expect((await selectedModules(page)).length).toBeGreaterThan(3);

    await page.getByRole('button', { name: 'Clear selection' }).click();
    await expect(reports).not.toHaveClass(SELECTED);
    // The whole point: a bulk convenience must not revoke a live licence.
    expect(await selectedModules(page)).toEqual(['ncollection_mis_templates']);
  });

  test('select visible results follows the search box', async ({ page }) => {
    const reports = page.locator(`${CARD}[data-module="ncollection_account_reports"]`);
    const crm = page.locator(`${CARD}[data-module="crm"]`);

    await page.getByRole('button', { name: 'Clear selection' }).click();
    await expect(reports).not.toHaveClass(SELECTED);

    await page.getByPlaceholder('Search modules…').fill('ncollection_account_report');
    // Wait for the FILTER to have settled — both halves, so the state is
    // unambiguous — before clicking. Clicking against an unsettled grid is
    // what made this test read either the pre- or post-filter card set.
    await expect(reports).toBeVisible();
    await expect(crm).toHaveCount(0);

    await page.getByRole('button', { name: 'Select visible results' }).click();
    await expect(reports).toHaveClass(SELECTED);
    // Nothing outside the filtered result may be swept in.
    expect(await selectedModules(page)).not.toContain('crm');
  });

  test('a core module is never selectable by click or by select all', async ({ page }) => {
    await page.getByPlaceholder('Search modules…').fill('');
    const core = page.locator(`${CARD}--core`).first();
    await expect(core).toBeVisible();
    // Core cards are rendered as divs, not buttons, and carry no data-module —
    // so they cannot enter the field by any path the widget offers.
    await expect(core).not.toHaveAttribute('data-module', /.+/);

    await page.getByRole('button', { name: 'Select all' }).click();
    const selected = await selectedModules(page);
    for (const coreName of ['base', 'ncollection_core', 'ncollection_branding', 'ncollection_auth']) {
      expect(selected, `${coreName} must never be written into the plan`).not.toContain(coreName);
    }
  });
});
